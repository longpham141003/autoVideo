from __future__ import annotations

import asyncio
import json
import math
import re
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .chatgpt_runner import ChatGPTWebRunner
from .config import APP_DIR
from .project_store import ProjectStore
from .text_to_voice_queue import TextToVoiceQueue


WORKFLOW_BY_CONTENT_MODE = {
    "growth_30": APP_DIR / "workflows" / "growth_story_5_chapters.json",
    "long_form": APP_DIR / "workflows" / "long_form_8_chapters.json",
    "auto_story": APP_DIR / "workflows" / "auto_story_daily_podcast.json",
}


class PipelineWorker(QThread):
    log_message = pyqtSignal(str)
    project_created = pyqtSignal(str)
    step_status = pyqtSignal(str, str, str)
    chapter_text_status = pyqtSignal(int, str, str)
    chapter_voice_status = pyqtSignal(int, str, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, settings: dict, input_data: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.input_data = input_data
        self.stop_requested = False
        self.project_dir: Path | None = None
        self.store = ProjectStore(settings.get("projects_dir"))
        self.state_lock = threading.Lock()
        self.voice_queue: TextToVoiceQueue | None = None
        self.last_artifacts: dict[str, str] = {}

    def stop(self) -> None:
        self.stop_requested = True
        if self.voice_queue:
            self.voice_queue.stop()

    def _log(self, message: str) -> None:
        text = str(message or "")
        self.log_message.emit(text)
        if self.project_dir:
            self.store.append_log(self.project_dir, text)

    def _voice_status(self, chapter_index: int, status: str, detail: str) -> None:
        if self.project_dir:
            with self.state_lock:
                payload = {"voice_status": status}
                if status == "done" and detail:
                    if Path(str(detail)).exists():
                        payload["voice_path"] = detail
                    else:
                        payload["voice_note"] = detail
                elif status == "error" and detail:
                    payload["voice_error"] = detail
                self.store.update_chapter(self.project_dir, chapter_index, **payload)
        self.chapter_voice_status.emit(int(chapter_index), str(status), str(detail or ""))

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self._log(f"Pipeline loi: {exc}")
            self.finished.emit(False, str(exc))

    async def _run_async(self) -> None:
        self.project_dir = self.store.create_project(self.input_data)
        self.project_created.emit(str(self.project_dir))
        self._log(f"Tao project: {self.project_dir.name}")
        if self._is_auto_story():
            self.input_data["auto_story_history"] = self._load_auto_story_history_text()

        workflow = self._load_workflow()
        chapter_count = int(self.input_data.get("chapter_count") or workflow.get("chapter_count") or 8)
        chapter_count = max(1, min(20, chapter_count))

        self.voice_queue = TextToVoiceQueue(
            self.settings,
            log=self._log,
            status=self._voice_status,
            max_workers=chapter_count,
        )
        self.voice_queue.start()

        runner = ChatGPTWebRunner(self.settings, log=self._log, stop_check=lambda: self.stop_requested)
        try:
            await runner.start()
            await runner.wait_for_login_ready(timeout_seconds=180)

            if isinstance(workflow.get("steps"), list):
                await self._run_steps_workflow(runner, workflow, chapter_count)
            else:
                await self._run_fast_workflow(runner, workflow, chapter_count)

            self._log("Da tao xong cac buoc text. Cho cac voice dang chay song song hoan tat...")
            await self._wait_for_voice_queue()
            if self._is_auto_story() and not self.stop_requested:
                self._save_auto_story_history()
            self.finished.emit(not self.stop_requested, "Hoan tat" if not self.stop_requested else "Da dung")
        finally:
            try:
                await runner.close()
            except Exception:
                pass
            if self.voice_queue and self.voice_queue.is_alive():
                self.voice_queue.stop()

    async def _run_fast_workflow(self, runner: ChatGPTWebRunner, workflow: dict, chapter_count: int) -> None:
        assert self.project_dir is not None
        story_bible = await self._run_step(runner, workflow, "story_bible", "summary_template", chapter_count)
        if self.stop_requested:
            raise RuntimeError("Stopped.")

        outline = await self._run_step(runner, workflow, "outline", "outline_template", chapter_count, {"story_bible": story_bible})
        if self.stop_requested:
            raise RuntimeError("Stopped.")

        previous = ""
        self.store.save_artifact(self.project_dir, "previous_chapters_summary", previous)
        for index in range(1, chapter_count + 1):
            if self.stop_requested:
                raise RuntimeError("Stopped.")
            self.chapter_text_status.emit(index, "running", "")
            self.store.update_chapter(self.project_dir, index, text_status="running", voice_status="waiting")
            prompt = self._render(
                workflow.get("chapter_template"),
                chapter_count=chapter_count,
                chapter_index=index,
                artifacts={
                    "story_bible": story_bible,
                    "outline": outline,
                    "previous_chapters_summary": previous or "No previous chapters yet.",
                },
            )
            raw_chapter_text = await runner.send_prompt(prompt, label=f"chapter_{index:02d}", timeout_seconds=1500)
            self._raise_if_chapter_not_narration(index, raw_chapter_text)
            chapter_text = raw_chapter_text
            chapter_text = self._clean_chapter_voice_text(chapter_text)
            self._raise_if_chapter_not_narration(index, chapter_text)
            chapter_text = await self._ensure_chapter_target_length(
                runner,
                index,
                chapter_count,
                chapter_text,
                context=f"STORY BIBLE:\n{story_bible}\n\nOUTLINE:\n{outline}",
            )
            text_path = self.store.save_chapter_text(self.project_dir, index, chapter_text)
            self._save_chapter_quality_report(index, chapter_text)
            self.chapter_text_status.emit(index, "done", str(text_path))
            self._log(f"Chapter {index:02d} da xong text, dua sang Text to Voice.")
            self._enqueue_voice(index, text_path)
            previous = self._append_local_recap(previous, index, chapter_text)
            self.store.save_artifact(self.project_dir, "previous_chapters_summary", previous)

    async def _run_steps_workflow(self, runner: ChatGPTWebRunner, workflow: dict, chapter_count: int) -> None:
        assert self.project_dir is not None
        artifacts: dict[str, str] = {}
        steps = [step for step in workflow.get("steps", []) if isinstance(step, dict)]
        for step in steps:
            if self.stop_requested:
                raise RuntimeError("Stopped.")

            step_id = str(step.get("id") or step.get("output") or "step").strip()
            output_id = self._output_id(step)
            chapter_index = self._chapter_index_from_output(output_id)
            if chapter_index and chapter_index > chapter_count:
                continue
            self.step_status.emit(step_id, "running", "")
            self.store.update_step(self.project_dir, step_id, "running", output=output_id)
            if chapter_index:
                self.chapter_text_status.emit(chapter_index, "running", "")
                self.store.update_chapter(self.project_dir, chapter_index, text_status="running", voice_status="waiting")

            try:
                text = await self._run_single_workflow_step(runner, step, artifacts, chapter_count)
                if chapter_index:
                    self._raise_if_chapter_not_narration(chapter_index, text)
                    text = self._clean_chapter_voice_text(text)
                    self._raise_if_chapter_not_narration(chapter_index, text)
                    text = await self._ensure_chapter_target_length(
                        runner,
                        chapter_index,
                        chapter_count,
                        text,
                        context=self._chapter_expansion_context(step, artifacts),
                    )
                artifacts[output_id] = text
                self.last_artifacts = dict(artifacts)
                artifact_path = self.store.save_artifact(self.project_dir, output_id, text)
                if output_id == "shorts_package":
                    self._materialize_shorts_package(text)

                if chapter_index:
                    text_path = self.store.save_chapter_text(self.project_dir, chapter_index, text)
                    self._save_chapter_quality_report(chapter_index, text)
                    self.step_status.emit(step_id, "done", str(text_path))
                    self.chapter_text_status.emit(chapter_index, "done", str(text_path))
                    self._log(f"Chapter {chapter_index:02d} da xong text, dua sang Text to Voice.")
                    self._enqueue_voice(chapter_index, text_path)
                else:
                    self.step_status.emit(step_id, "done", str(artifact_path))
            except Exception as exc:
                self.step_status.emit(step_id, "error", str(exc))
                if chapter_index:
                    self.chapter_text_status.emit(chapter_index, "error", str(exc))
                raise

    async def _run_single_workflow_step(self, runner: ChatGPTWebRunner, step: dict, artifacts: dict[str, str], chapter_count: int) -> str:
        assert self.project_dir is not None
        step_id = str(step.get("id") or "")
        output_id = self._output_id(step)

        if step_id == "extract_thumb_text":
            manual_thumb = str(self.input_data.get("original_thumb_text") or "").strip()
            if manual_thumb:
                self._log("Dung thumb text nguoi dung da nhap, bo qua buoc doc anh thumb.")
                return manual_thumb

        missing = self._missing_requirements(step, artifacts)
        if missing:
            raise RuntimeError(f"Thieu input cho {step_id}: {', '.join(missing)}")

        action = str(step.get("action") or "ai").strip()
        if action == "concat":
            return self._concat_step(step, artifacts)
        if action == "chunked_ai":
            return await self._run_chunked_step(runner, step, artifacts, chapter_count)

        prompt = self._render(step.get("template"), chapter_count=chapter_count, artifacts=artifacts)
        image_path = ""
        if step.get("image") == "thumbnail":
            image_path = str(self.input_data.get("thumbnail_path") or "").strip()
        return await runner.send_prompt(prompt, label=output_id, timeout_seconds=1200, image_path=image_path)

    async def _run_chunked_step(self, runner: ChatGPTWebRunner, step: dict, artifacts: dict[str, str], chapter_count: int) -> str:
        assert self.project_dir is not None
        output_id = self._output_id(step)
        source_key = str(step.get("source") or "input.original_script")
        source_text = self._resolve_value(source_key, artifacts)
        chunks = self._split_text_chunks(source_text, int(step.get("chunkChars") or 18000))
        if not chunks:
            raise RuntimeError(f"Khong co source text cho {output_id}")

        summaries: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            if self.stop_requested:
                raise RuntimeError("Stopped.")
            self._log(f"{output_id}: gui chunk {index}/{len(chunks)}")
            prompt = self._render(
                step.get("chunkTemplate") or step.get("template"),
                chapter_count=chapter_count,
                artifacts=artifacts,
                extra={
                    "chunk.index": str(index),
                    "chunk.count": str(len(chunks)),
                    "chunk.text": chunk,
                },
            )
            answer = await runner.send_prompt(prompt, label=f"{output_id}_part_{index:02d}", timeout_seconds=1200)
            summaries.append(f"PART {index}/{len(chunks)}\n{answer}")
            self.store.save_artifact(self.project_dir, f"{output_id}.part{index:02d}", answer)

        merge_prompt = self._render(
            step.get("mergeTemplate") or step.get("template"),
            chapter_count=chapter_count,
            artifacts=artifacts,
            extra={"chunk_summaries": "\n\n".join(summaries)},
        )
        return await runner.send_prompt(merge_prompt, label=f"{output_id}_merge", timeout_seconds=1200)

    def _concat_step(self, step: dict, artifacts: dict[str, str]) -> str:
        parts = []
        include_labels = step.get("includeSourceLabels") is not False
        for source_id in step.get("sources") or []:
            text = str(artifacts.get(str(source_id)) or "").strip()
            if not text:
                continue
            if include_labels:
                parts.append(f"{str(source_id).replace('_', ' ').upper()}\n\n{text}")
            else:
                parts.append(text)
        return "\n\n".join(parts).strip() + "\n"

    async def _ensure_chapter_target_length(
        self,
        runner: ChatGPTWebRunner,
        chapter_index: int,
        chapter_count: int,
        chapter_text: str,
        *,
        context: str = "",
    ) -> str:
        target_words = self._chapter_word_count(chapter_count)
        minimum_words = max(250, int(target_words * 0.90))
        current_words = len(str(chapter_text or "").split())
        if current_words >= minimum_words:
            return chapter_text

        self._log(
            f"Chapter {int(chapter_index):02d} qua ngan: {current_words}/{target_words} tu. "
            "Gui lai ChatGPT de mo rong truoc khi tao voice."
        )
        prompt = self._chapter_expansion_prompt(
            chapter_index=chapter_index,
            chapter_count=chapter_count,
            current_text=chapter_text,
            target_words=target_words,
            minimum_words=minimum_words,
            context=context,
        )
        expanded = await runner.send_prompt(
            prompt,
            label=f"chapter_{int(chapter_index):02d}_expand",
            timeout_seconds=1500,
        )
        expanded = self._clean_chapter_voice_text(expanded)
        self._raise_if_chapter_not_narration(chapter_index, expanded)
        expanded_words = len(expanded.split())
        if expanded_words < minimum_words:
            self._log(
                f"Chapter {int(chapter_index):02d} van ngan sau khi mo rong: "
                f"{expanded_words}/{target_words} tu. Tiep tuc voi ban dai nhat hien co."
            )
            return expanded if expanded_words > current_words else chapter_text
        return expanded

    def _chapter_expansion_context(self, step: dict, artifacts: dict[str, str]) -> str:
        parts: list[str] = []
        for req in step.get("requires") or []:
            key = str(req or "")
            if not key.startswith("artifact."):
                continue
            artifact_id = key[9:]
            value = str(artifacts.get(artifact_id) or "").strip()
            if value:
                parts.append(f"{artifact_id.upper()}:\n{value}")
        return "\n\n".join(parts)

    def _chapter_expansion_prompt(
        self,
        *,
        chapter_index: int,
        chapter_count: int,
        current_text: str,
        target_words: int,
        minimum_words: int,
        context: str = "",
    ) -> str:
        return (
            "Rewrite and expand this chapter into a full voiceover-ready narration script.\n\n"
            f"Chapter: {int(chapter_index)} of {int(chapter_count)}\n"
            f"Target length: around {int(target_words)} words.\n"
            f"Minimum acceptable length: {int(minimum_words)} words.\n\n"
            "Hard rule: do not answer until the chapter is at least the minimum length. "
            "Keep the same story events and continuity, but expand with concrete action, dialogue, text messages, calls, "
            "bank/legal documents, visible reactions, consequences, and internal decisions. Do not pad with generic reflection.\n\n"
            "Scope lock: do not add federal agents, FBI, Treasury, helicopters, guns, encrypted drives, laundering networks, "
            "shell companies, cartels, SWAT, kidnapping, murder, chases, or action-thriller set pieces unless they already exist "
            "explicitly in the approved outline. Keep legal or bank details ordinary, local, and tied to the personal conflict.\n\n"
            "Return only finished first-person American English narration. No title, no chapter label, no bullets, no markdown, "
            "no notes, no outline, no timestamps, no production labels. Use straight double quotes for dialogue.\n\n"
            f"CONTEXT:\n{str(context or '').strip()[:9000]}\n\n"
            "CURRENT SHORT DRAFT TO EXPAND:\n"
            f"{str(current_text or '').strip()}\n"
        )

    async def _wait_for_voice_queue(self) -> None:
        if not self.voice_queue:
            return
        self.voice_queue.finish_when_empty()
        while self.voice_queue.is_alive():
            if self.stop_requested:
                self.voice_queue.stop()
                break
            await asyncio.sleep(0.5)

    def _missing_requirements(self, step: dict, artifacts: dict[str, str]) -> list[str]:
        missing = []
        for req in step.get("requires") or []:
            if not self._resolve_value(str(req), artifacts).strip():
                missing.append(str(req))
        if step.get("image") == "thumbnail":
            thumb_text = str(self.input_data.get("original_thumb_text") or "").strip()
            thumb_path = str(self.input_data.get("thumbnail_path") or "").strip()
            if not thumb_text and not (thumb_path and Path(thumb_path).exists()):
                missing.append("thumbnail_path hoac original_thumb_text")
        return missing

    def _resolve_value(self, key: str, artifacts: dict[str, str]) -> str:
        name = str(key or "").strip()
        if name == "computed.chapter_word_count":
            return str(self._chapter_word_count(int(self.input_data.get("chapter_count") or 8)))
        if name == "computed.target_word_count":
            return str(self.input_data.get("target_word_count") or "")
        if name.startswith("input."):
            return str(self.input_data.get(name[6:]) or "")
        if name.startswith("artifact."):
            return str(artifacts.get(name[9:]) or "")
        return ""

    @staticmethod
    def _output_id(step: dict) -> str:
        return str(step.get("output") or step.get("id") or "output").strip()

    @staticmethod
    def _chapter_index_from_output(output_id: str) -> int:
        match = re.fullmatch(r"chapter_(\d+)", str(output_id or "").strip())
        return int(match.group(1)) if match else 0

    @staticmethod
    def _split_text_chunks(text: str, max_chars: int) -> list[str]:
        source = str(text or "").strip()
        if not source:
            return []
        max_chars = max(2000, int(max_chars or 18000))
        if len(source) <= max_chars:
            return [source]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        def flush() -> None:
            nonlocal current, current_len
            if current:
                chunks.append("\n\n".join(current).strip())
                current = []
                current_len = 0

        for raw in re.split(r"\n\s*\n", source):
            paragraph = raw.strip()
            if not paragraph:
                continue
            if len(paragraph) > max_chars:
                flush()
                words = paragraph.split()
                piece: list[str] = []
                piece_len = 0
                for word in words:
                    add = len(word) + (1 if piece else 0)
                    if piece and piece_len + add > max_chars:
                        chunks.append(" ".join(piece))
                        piece = [word]
                        piece_len = len(word)
                    else:
                        piece.append(word)
                        piece_len += add
                if piece:
                    chunks.append(" ".join(piece))
                continue
            add = len(paragraph) + (2 if current else 0)
            if current and current_len + add > max_chars:
                flush()
            current.append(paragraph)
            current_len += add
        flush()
        return chunks

    def _load_workflow(self) -> dict:
        mode = str(self.input_data.get("content_mode") or "").strip()
        preset_path = WORKFLOW_BY_CONTENT_MODE.get(mode)
        path = preset_path if preset_path and preset_path.exists() else Path(str(self.settings.get("workflow_path") or ""))
        if not path.exists():
            raise FileNotFoundError(f"Khong thay workflow: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Workflow JSON khong hop le.")
        return data

    def _is_auto_story(self) -> bool:
        return str(self.input_data.get("content_mode") or "") == "auto_story"

    def _auto_story_history_path(self) -> Path:
        return Path(str(self.settings.get("projects_dir") or APP_DIR / "Projects")) / "auto_story_history.json"

    def _load_auto_story_history_text(self) -> str:
        path = self._auto_story_history_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = []
        if not isinstance(data, list):
            return "No previous auto stories yet."
        genre = str(self.input_data.get("auto_genre") or "").strip()
        rows = []
        for item in data[-80:]:
            if not isinstance(item, dict):
                continue
            if genre and str(item.get("genre") or "") != genre:
                continue
            rows.append(
                "- {title} | {setting} | {premise} | twist: {twist}".format(
                    title=str(item.get("story_title") or "untitled")[:120],
                    setting=str(item.get("setting") or "")[:120],
                    premise=str(item.get("premise_signature") or "")[:220],
                    twist=str(item.get("core_twist") or "")[:160],
                )
            )
        return "\n".join(rows[-30:]) if rows else "No previous auto stories for this genre yet."

    def _save_auto_story_history(self) -> None:
        if not self.project_dir:
            return
        path = self._auto_story_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
        package_text = self.last_artifacts.get("episode_package") or ""
        package = self._parse_json_object(package_text)
        concept = self.last_artifacts.get("story_concept") or ""
        bible = self.last_artifacts.get("continuity_bible") or ""
        title = str(self.input_data.get("original_title") or "").strip()
        if isinstance(package, dict):
            title = str(package.get("title") or title).strip()
        data.append(
            {
                "created_at": self.store.load_state(self.project_dir).get("created_at", ""),
                "genre": str(self.input_data.get("auto_genre") or ""),
                "genre_label": str(self.input_data.get("auto_genre_label") or ""),
                "age_segment": str(self.input_data.get("auto_age_segment") or ""),
                "story_title": title or self.project_dir.name,
                "premise_signature": " ".join(concept.split())[:600],
                "setting": self._extract_history_field(bible + "\n" + concept, "setting"),
                "core_twist": self._extract_history_field(concept + "\n" + bible, "twist"),
                "project_dir": str(self.project_dir),
            }
        )
        path.write_text(json.dumps(data[-300:], ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"Da luu lich su Auto Story: {path}")

    @staticmethod
    def _extract_history_field(text: str, keyword: str) -> str:
        source = str(text or "")
        pattern = rf"(?im)^\s*(?:[-*]\s*)?(?:core\s+)?{re.escape(keyword)}\s*[:\-]\s*(.+)$"
        match = re.search(pattern, source)
        if match:
            return " ".join(match.group(1).split())[:220]
        return ""

    async def _run_step(self, runner: ChatGPTWebRunner, workflow: dict, artifact_id: str, template_key: str, chapter_count: int, artifacts: dict | None = None) -> str:
        assert self.project_dir is not None
        self.step_status.emit(artifact_id, "running", "")
        self.store.update_step(self.project_dir, artifact_id, "running")
        prompt = self._render(workflow.get(template_key), chapter_count=chapter_count, artifacts=artifacts or {})
        text = await runner.send_prompt(prompt, label=artifact_id, timeout_seconds=1200)
        path = self.store.save_artifact(self.project_dir, artifact_id, text)
        self.step_status.emit(artifact_id, "done", str(path))
        return text

    def _enqueue_voice(self, chapter_index: int, text_path: Path) -> None:
        assert self.project_dir is not None
        if not self.voice_queue:
            return
        out_path = self.store.expected_voice_path(self.project_dir, chapter_index)
        self.voice_queue.enqueue(chapter_index, text_path=str(text_path), output_path=str(out_path))

    def _save_chapter_quality_report(self, chapter_index: int, text: str) -> None:
        if not self.project_dir:
            return
        warnings = self._chapter_quality_warnings(chapter_index, text)
        word_count = len(str(text or "").split())
        critical_warnings = [item for item in warnings if item.startswith("BLOCKER:")]
        report = [
            f"Chapter {int(chapter_index):02d} QA",
            f"Words: {word_count}",
            f"Mode: {self.input_data.get('content_mode_label') or self.input_data.get('content_mode') or ''}",
            "",
        ]
        if warnings:
            report.append("Warnings:")
            report.extend(f"- {item}" for item in warnings)
            self._log(f"QA chapter {int(chapter_index):02d}: {len(warnings)} canh bao truoc khi tao voice.")
        else:
            report.append("Warnings: none")
        self.store.save_artifact(self.project_dir, f"script_qa_chapter_{int(chapter_index):02d}", "\n".join(report))
        if critical_warnings:
            message = (
                f"Chapter {int(chapter_index):02d} failed story QA before Text to Voice: "
                + " | ".join(item.replace("BLOCKER: ", "", 1) for item in critical_warnings[:3])
            )
            self._log(message)
            raise RuntimeError(message)

    def _materialize_shorts_package(self, text: str) -> None:
        if not self.project_dir:
            return
        shorts_dir = self.project_dir / "shorts"
        shorts_dir.mkdir(parents=True, exist_ok=True)
        raw = str(text or "").strip()
        (shorts_dir / "shorts_package_raw.txt").write_text(raw + "\n", encoding="utf-8")

        data = self._parse_json_object(raw)
        if not isinstance(data, dict):
            self._log("Shorts package: khong parse duoc JSON, da luu raw text.")
            return

        (shorts_dir / "shorts_package.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        shorts = data.get("shorts")
        if not isinstance(shorts, list):
            self._log("Shorts package: JSON khong co list shorts.")
            return

        validation = self._validate_shorts_package(shorts)
        (shorts_dir / "shorts_validation.txt").write_text("\n".join(validation).strip() + "\n", encoding="utf-8")
        if any(line.startswith("WARNING") for line in validation):
            self._log("Shorts package: co canh bao format, xem shorts_validation.txt.")

        for index, item in enumerate(shorts, start=1):
            if not isinstance(item, dict):
                continue
            short_id = str(item.get("id") or f"short_{index:02d}").strip() or f"short_{index:02d}"
            safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", short_id).strip("_") or f"short_{index:02d}"
            short_dir = shorts_dir / safe_id
            short_dir.mkdir(parents=True, exist_ok=True)
            (short_dir / "metadata.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            blocks = item.get("veo_blocks")
            block_voices: list[str] = []
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict):
                        voice = str(block.get("voice") or "").strip()
                        if voice:
                            block_voices.append(voice)
            voice_script = "\n\n".join(block_voices) if block_voices else item.get("voice_script")
            self._write_short_text_file(short_dir / "voice.txt", voice_script)
            self._write_short_text_file(short_dir / "cover_image_prompt.txt", item.get("cover_image_prompt"))
            self._write_short_text_file(short_dir / "cover_negative_prompt.txt", item.get("cover_negative_prompt"))
            self._write_short_text_file(short_dir / "cta.txt", item.get("cta_voice_line"))
            self._write_short_text_file(short_dir / "caption_overlay.txt", item.get("caption_overlay"))
            self._write_short_text_file(short_dir / "related_video_cta.txt", item.get("related_video_cta"))
            if isinstance(blocks, list):
                for block_index, block in enumerate(blocks, start=1):
                    if not isinstance(block, dict):
                        continue
                    prefix = short_dir / f"block_{block_index:02d}"
                    self._write_short_text_file(prefix.with_suffix(".voice.txt"), block.get("voice"))
                    self._write_short_text_file(prefix.with_suffix(".veo_prompt.txt"), block.get("veo_prompt"))
                    self._write_short_text_file(prefix.with_suffix(".image_prompt.txt"), block.get("image_prompt") or block.get("veo_prompt"))
                    self._write_short_text_file(prefix.with_suffix(".negative_prompt.txt"), block.get("negative_prompt"))
        self._log(f"Shorts package: da tach {len(shorts)} shorts vao {shorts_dir}")

    @staticmethod
    def _validate_shorts_package(shorts: list) -> list[str]:
        lines = ["Shorts package validation"]
        if len(shorts) != 2:
            lines.append(f"WARNING: expected exactly 2 shorts, got {len(shorts)}.")
        expected = [(24, 3), (40, 5)]
        for index, item in enumerate(shorts[:2], start=1):
            if not isinstance(item, dict):
                lines.append(f"WARNING: short {index} is not an object.")
                continue
            expected_duration, expected_blocks = expected[index - 1]
            duration = item.get("duration_seconds")
            blocks = item.get("veo_blocks")
            if int(duration or 0) != expected_duration:
                lines.append(f"WARNING: short {index} duration should be {expected_duration}s, got {duration}.")
            if not isinstance(blocks, list) or len(blocks) != expected_blocks:
                count = len(blocks) if isinstance(blocks, list) else 0
                lines.append(f"WARNING: short {index} should have {expected_blocks} VEO blocks, got {count}.")
            final_voice = ""
            if isinstance(blocks, list) and blocks:
                final = blocks[-1]
                if isinstance(final, dict):
                    final_voice = str(final.get("voice") or "")
            cta = f"{item.get('cta_voice_line') or ''} {final_voice}".lower()
            if "subscribe" not in cta or "bell" not in cta:
                lines.append(f"WARNING: short {index} final CTA should include subscribe and bell.")
        if len(lines) == 1:
            lines.append("OK")
        return lines

    @staticmethod
    def _write_short_text_file(path: Path, value) -> None:
        text = str(value or "").strip()
        if text:
            path.write_text(text + "\n", encoding="utf-8")

    @staticmethod
    def _parse_json_object(text: str) -> dict | None:
        raw = str(text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _chapter_quality_warnings(self, chapter_index: int, text: str) -> list[str]:
        source = str(text or "").strip()
        warnings: list[str] = []
        if not source:
            return ["Chapter text is empty."]

        if self._looks_like_production_outline(source):
            warnings.append("Chapter contains outline/production labels; do not send this text to voice.")

        compact = " ".join(source.split())
        compact_lower = compact.lower()
        words = compact.split()
        first_words = " ".join(words[:140]).lower()
        first_30 = " ".join(words[:90]).lower()
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", compact) if item.strip()]
        first_sentences = " ".join(sentences[:6]).lower()

        leaked_labels = [
            "proof object",
            "emotional knife",
            "payoff tease",
            "retention beat",
            "escalation engine",
            "premise strength",
            "thumbnail concept",
            "open loop",
        ]
        leaked = [label for label in leaked_labels if label in compact_lower]
        if leaked:
            warnings.append(
                "Chapter contains prompt/meta wording in narration: "
                + ", ".join(leaked)
                + ". Rewrite as natural story prose."
            )

        evidence_terms = re.findall(
            r"\b(?:timestamp(?:ed)?|email|pdf|screenshot|proof|evidence|document|file|files)\b",
            compact_lower,
        )
        if len(evidence_terms) >= 18:
            warnings.append("Evidence wording is repetitive; vary proof types, scenes, and consequences.")

        if self._is_auto_story():
            if chapter_index == 1 and re.search(r"\b(hello|welcome back|before we begin|in this episode|today i want to)\b", first_30):
                warnings.append("Opening has intro/preamble language. Start directly on story action.")
            if len(re.findall(r"[\"â€œâ€]", source)) < 2:
                warnings.append("Very little direct dialogue or quoted evidence; consider sharper spoken moments.")
            long_paragraphs = [p for p in re.split(r"\n\s*\n", source) if len(p.split()) > 170]
            if long_paragraphs:
                warnings.append(f"{len(long_paragraphs)} paragraph(s) exceed 170 words; add paragraph breaks/pacing.")
            concrete_hits = len(re.findall(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|message|call|letter|record|photo|file|room|street|house|office|station|hospital|bank|court|school|ship|city|town)\b", compact, flags=re.IGNORECASE))
            if concrete_hits < 6:
                warnings.append("Low concrete-detail density; add specific objects, places, dates, messages, records, or sensory anchors.")
            if chapter_index > 1 and not re.search(r"\b(then|but|that was when|what i didn't know|by the time|the next|before i could)\b", " ".join(words[-80:]).lower()):
                warnings.append("Chapter ending may not have a strong enough open loop or forward pull.")
            return warnings

        drift_patterns = [
            r"\bfederal\s+(?:agent|agents|case|investigation|task force|taskforce)\b",
            r"\b(?:fbi|cia|swat|homeland security|treasury agents?|special agents?)\b",
            r"\b(?:helicopter|chopper|tactical|surveillance van|safe house|sting operation)\b",
            r"\b(?:encrypted drive|encrypted files?|hard drive|thumb drive)\b",
            r"\b(?:money laundering|laundering network|shell compan(?:y|ies)|offshore account)\b",
            r"\b(?:cartel|hostage|kidnapping|kidnapped|murder|assassination)\b",
            r"\b(?:revolver|handgun|pistol|rifle|gunfire|weapon|armed guards?)\b",
            r"\b(?:car chase|airport escape|border crossing|bunker|cabin trap)\b",
        ]
        drift_hits: list[str] = []
        for pattern in drift_patterns:
            drift_hits.extend(re.findall(pattern, compact_lower))
        if drift_hits:
            unique_hits = sorted({str(hit) for hit in drift_hits})[:8]
            warnings.append(
                "BLOCKER: Story drift into crime/action thriller terms detected: "
                + ", ".join(unique_hits)
                + ". Keep conflict grounded in the original family/work/wedding premise."
            )

        premise_text = " ".join(
            str(self.input_data.get(key) or "")
            for key in (
                "original_title",
                "new_title_en",
                "original_thumb_text",
                "content_mode_label",
            )
        ).lower()
        wedding_premise = bool(re.search(r"\b(wedding|bride|groom|married|marry|ceremony|reception)\b", premise_text))
        if wedding_premise and chapter_index <= 6:
            wedding_terms = re.findall(
                r"\b(wedding|bride|groom|rehearsal|reception|ceremony|aisle|venue|vows|dress|bouquet|florist|photographer|caterer|honeymoon|married)\b",
                compact_lower,
            )
            family_money_terms = re.findall(r"\b(parent|mother|father|husband|bank|account|card|payment|venue|deposit|refund|lawyer|attorney)\b", compact_lower)
            if len(wedding_terms) < 2 and len(family_money_terms) < 4:
                warnings.append(
                    "Story may be drifting away from the wedding premise; keep scenes tied to the ceremony, reception, family money, or immediate aftermath."
                )

        legal_action_terms = re.findall(
            r"\b(court|judge|lawsuit|police|detective|investigation|warrant|subpoena|felony|criminal|arrest|trial|evidence|case file)\b",
            compact_lower,
        )
        grounded_terms = re.findall(
            r"\b(family|mother|father|parent|sister|brother|husband|wife|text|call|bank|account|payment|home|kitchen|venue|reception|lawyer|attorney)\b",
            compact_lower,
        )
        if len(legal_action_terms) >= 10 and len(grounded_terms) < 8:
            warnings.append(
                "BLOCKER: Legal/action details dominate the chapter. Rewrite as grounded personal drama with ordinary bank, attorney, family, or workplace consequences."
            )

        if chapter_index == 1:
            if re.search(r"\b(hello|welcome back|before we begin|in this video|today i want to)\b", first_30):
                warnings.append("Opening has intro/preamble language. Start directly on betrayal or consequence.")
            if not re.search(r"\b(my name is|i'm|i am|my|me|i)\b", first_words):
                warnings.append("Opening may not anchor the protagonist quickly.")
            if not re.search(r"\b(told|texted|called|said|sent|refused|left|cut|fired|cancelled|canceled|locked|blocked|laughed|humiliated|betrayed)\b", first_words):
                warnings.append("Opening may not show the betrayal/action quickly enough.")
            if not re.search(r"\b(text|message|call|voicemail|email|recording|photo|video|bill|receipt|document|contract|letter|account|payment|check|notice|screenshot|file)\b", first_words):
                warnings.append("Opening may lack a concrete proof object or visible consequence.")
            first_80_words = " ".join(words[:80]).lower()
            has_specific_weapon = re.search(
                r"\b(?:recording|voicemail|email|message|text|screenshot|photo|video|bill|receipt|check|letter|notice|"
                r"contract|lawsuit|police report|lawyer|attorney|payment|account|document|deed|will|payroll|venue|board email)\b",
                first_80_words,
            )
            has_specific_consequence = re.search(
                r"\b(?:froze|frozen|failed|locked|canceled|cancelled|served|filed|scheduled|unsigned|stopped|bounced|called in|"
                r"lost|shut down|cut off|blocked)\b",
                first_80_words,
            )
            if not (has_specific_weapon and has_specific_consequence):
                warnings.append("Opening first 80 words need one specific proof object or consequence, not vague planning.")
            if re.search(r"\b(proof|plan|leverage|files|receipts|strategy)\b", first_80_words) and not has_specific_weapon:
                warnings.append("Opening uses vague proof/plan/leverage wording without naming the exact weapon.")
            if re.search(r"\b(florist|flowers|coffee|kitchen|sunlight|weather|dress|venue|room|air felt|staring at my phone)\b", first_80_words):
                warnings.append("Opening may spend early time on atmosphere instead of emotional violence.")
            telling_hits = len(re.findall(r"\b(i felt|i realized|i could feel|i knew|i understood|i remembered)\b", " ".join(words[:180]).lower()))
            if telling_hits >= 3:
                warnings.append("Opening has too much telling; make the situation create the emotion.")
            if not re.search(r"[\"“”]", " ".join(words[:180])):
                warnings.append("Opening may lack an exact quote/message.")
            if len(first_sentences.split()) > 170:
                warnings.append("First 5-6 sentences may be too slow; tighten the first 30 seconds.")

        long_paragraphs = [p for p in re.split(r"\n\s*\n", source) if len(p.split()) > 150]
        if long_paragraphs:
            warnings.append(f"{len(long_paragraphs)} paragraph(s) exceed 150 words; add paragraph breaks/pacing.")

        if len(re.findall(r"[\"“”]", source)) < 2:
            warnings.append("Very little direct dialogue/text-message quoting; niche usually needs sharp quotes.")

        concrete_hits = len(re.findall(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|dollar|bank|lawyer|court|police|document|account|payment|mortgage|trust|deed|will|contract|text|message|call|missed calls?)\b", compact, flags=re.IGNORECASE))
        if concrete_hits < 8:
            warnings.append("Low concrete-detail density; add dates, money, messages, calls, documents, or legal/bank details.")

        if chapter_index > 1 and not re.search(r"\b(then|but|that was when|what i didn't know|by the time|the next|before i could)\b", " ".join(words[-80:]).lower()):
            warnings.append("Chapter ending may not have a strong open loop.")

        if re.search(r"\b(like and subscribe|hit subscribe|drop a comment)\b", first_30):
            warnings.append("CTA appears too early; keep the first 30 seconds focused on payoff.")

        return warnings

    @staticmethod
    def _looks_like_production_outline(text: str) -> bool:
        source = str(text or "")
        if not source.strip():
            return False
        markers = [
            r"\bEmotional Goal\s*:",
            r"\bMini-hook\s*:",
            r"\bConflict\s*:",
            r"\bDialogue\s*:",
            r"\bVisual\s*:",
            r"\bImage[_ -]?prompt\s*:",
            r"\bVEO[_ -]?prompt\s*:",
            r"\bVideo[_ -]?prompt\s*:",
            r"\bNegative[_ -]?prompt\s*:",
            r"\bVisual[_ -]?context\s*:",
            r"\bCaption[_ -]?overlay\s*:",
            r"\bAction\s*:",
            r"\bInternal line\s*:",
            r"\bImmediate reaction\s*:",
            r"\bSEO\s*&?\s*Retention\s*:",
            r"\bRetention\s*:",
            r"\bOpen-loop line\s*:",
            r"\bScene\s+\d+\s*[–—:-]",
            r"\bBeat\s+\d+\s*[–—:-]",
        ]
        hits = sum(len(re.findall(pattern, source, flags=re.IGNORECASE)) for pattern in markers)
        if hits >= 3:
            return True
        compact = " ".join(source.split())
        first_120 = " ".join(compact.split()[:120])
        return bool(
            re.search(r"\bEmotional Goal\s*:.*\bMini-hook\s*:.*\bConflict\s*:", first_120, flags=re.IGNORECASE)
            or re.search(r"\bScene\s+1\s*[–—:-].*\bScene\s+2\s*[–—:-]", compact, flags=re.IGNORECASE)
        )

    def _raise_if_chapter_not_narration(self, chapter_index: int, text: str) -> None:
        if self._looks_like_production_outline(text):
            cleaned = self._clean_chapter_voice_text(text)
            if cleaned and not self._looks_like_production_outline(cleaned) and len(cleaned.split()) >= 25:
                return
            message = (
                f"Chapter {int(chapter_index):02d} looks like outline/production notes, not voice-ready narration. "
                "Stopped before Text to Voice to avoid creating a low-quality video."
            )
            self._log(message)
            raise RuntimeError(message)

    def _voice_language(self) -> str:
        language = str(self.input_data.get("output_language") or "English").lower()
        if "viet" in language or "vi" == language:
            return "vi"
        return "en"

    @staticmethod
    def _clean_chapter_voice_text(text: str) -> str:
        source = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not source:
            return ""
        json_source = PipelineWorker._extract_tts_json_payload(source)
        if json_source:
            source = json_source
        source = re.sub(r"```(?:text|plain|markdown|md|)?\s*", "", source, flags=re.IGNORECASE)
        source = source.replace("```", "")
        source = PipelineWorker._normalize_tts_safe_text(source)

        cleaned: list[str] = []
        skip_line = re.compile(
            r"^\s*(?:"
            r"#{1,6}\s*.+|"
            r"(?:chapter|part)\s+(?:\d+|one|two|three|four|five|six|seven|eight)\b.*|"
            r"(?:title|chapter title|word count|target word count|voiceover|voice over|narrator|script|plain text|output)\s*:.*|"
            r"(?:image[_ -]?prompt|veo[_ -]?prompt|video[_ -]?prompt|negative[_ -]?prompt|visual[_ -]?context|caption[_ -]?overlay|metadata|duration[_ -]?seconds|blocks|seo|retention)\s*:.*|"
            r"(?:emotional goal|mini-hook|conflict|visual|action|internal line|immediate reaction)\s*:.*|"
            r"(?:scene|beat)\s+\d+\b.*|"
            r"(?:dialogue|father|mother|sister|brother|assistant|bridesmaid|coordinator|charlie|olivia|daniel|noah|samuel)\s*:?\s*$|"
            r"(?:sure|of course|absolutely)[,\s.!-]*(?:here|below|i).+|"
            r"here(?:'s| is)\s+.*(?:chapter|voiceover|voice over|script|narration).*|"
            r"(?:end of chapter|chapter ends?|to be continued)\b.*"
            r")\s*$",
            re.IGNORECASE,
        )
        bullet_line = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s+")
        bracket_cue_line = re.compile(r"^\s*[\[(][^\])]{1,80}(?:pause|sigh|music|beat|angry|whisper|cry|laugh|breath|silence|cut)[^\])]*[\])]\s*$", re.IGNORECASE)
        plain_cue_line = re.compile(r"^\s*(?:pause|sigh|music|beat|angry|whisper|cry|laugh|breath|silence|cut)(?:\b.*)?$", re.IGNORECASE)

        for raw_line in source.split("\n"):
            line = raw_line.strip()
            if not line:
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
                continue
            handled_metadata, line = PipelineWorker._extract_tts_metadata_line(line)
            if handled_metadata and not line:
                continue
            if skip_line.match(line) or bracket_cue_line.match(line) or plain_cue_line.match(line):
                continue
            line = re.sub(r"^\s*(?:#{1,6}\s*)", "", line).strip()
            line = bullet_line.sub("", line).strip()
            line = re.sub(r"\b(?:Emotional Goal|Mini-hook|Conflict|Visual|Action|Internal line|Immediate reaction)\s*:\s*", "", line, flags=re.IGNORECASE)
            line = re.sub(r"\b(?:Image prompt|VEO prompt|Video prompt|Negative prompt|Caption overlay|Visual context)\s*:\s*[^.?!]{0,260}", "", line, flags=re.IGNORECASE)
            line = re.sub(r"\b(?:Scene|Beat)\s+\d+\s*[-:]\s*[^.?!]{0,80}", "", line, flags=re.IGNORECASE)
            line = re.sub(r"\bDialogue\s*:\s*", "", line, flags=re.IGNORECASE)
            line = re.sub(r"\b(?:Father|Mother|Sister|Brother|Assistant|Bridesmaid|Coordinator|Charlie|Olivia|Daniel|Noah|Samuel)\s*:\s*", "", line)
            line = re.sub(r"\[(?:pause|sigh|music|beat|angry|whisper|cry|laugh|breath|silence|cut)[^\]]*\]", "", line, flags=re.IGNORECASE)
            line = re.sub(r"\((?:pause|sigh|music|beat|angry|whisper|cry|laugh|breath|silence|cut)[^)]*\)", "", line, flags=re.IGNORECASE)
            if line:
                cleaned.append(line)

        result = "\n".join(cleaned).strip()
        result = re.sub(r"\n{3,}", "\n\n", result)
        return PipelineWorker._smooth_voice_paragraphs(result).strip()

    @staticmethod
    def _extract_tts_metadata_line(line: str) -> tuple[bool, str]:
        match = re.match(r"^\s*[\"']?([A-Za-z][A-Za-z0-9_ -]{0,40})[\"']?\s*:\s*(.*?)[,;]?\s*$", str(line or "").strip())
        if not match:
            return False, str(line or "").strip()
        key = re.sub(r"\s+", " ", match.group(1).replace("_", " ")).strip().lower()
        raw_value = match.group(2).strip().strip(",").strip()
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"\"", "'"}:
            raw_value = raw_value[1:-1].strip()
        skip_keys = {
            "image prompt",
            "image prompts",
            "veo prompt",
            "veo prompts",
            "video prompt",
            "video prompts",
            "negative prompt",
            "visual",
            "visual context",
            "caption overlay",
            "prompt",
            "metadata",
            "duration seconds",
            "blocks",
            "seo",
            "retention",
        }
        keep_value_keys = {
            "voice",
            "voiceover",
            "voice over",
            "narrator",
            "narration",
            "script",
            "script text",
            "plain text",
            "spoken text",
            "text",
        }
        if key in skip_keys:
            return True, ""
        if key in keep_value_keys:
            return True, raw_value
        return False, str(line or "").strip()

    @staticmethod
    def _extract_tts_json_payload(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"^```(?:json|text|plain|markdown|md)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
        candidates = [raw]
        for opener, closer in (("{", "}"), ("[", "]")):
            start = raw.find(opener)
            end = raw.rfind(closer)
            if 0 <= start < end:
                candidates.append(raw[start:end + 1])
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except Exception:
                continue
            values = PipelineWorker._collect_tts_json_values(data)
            if values:
                return "\n\n".join(values)
        return ""

    @staticmethod
    def _collect_tts_json_values(data: object, parent_key: str = "") -> list[str]:
        keep_keys = {"voice", "voiceover", "voice over", "narrator", "narration", "script", "script text", "plain text", "spoken text", "text"}
        skip_keys = {
            "image prompt",
            "image prompts",
            "veo prompt",
            "veo prompts",
            "video prompt",
            "video prompts",
            "negative prompt",
            "visual",
            "visual context",
            "caption overlay",
            "cover image prompt",
            "prompt",
            "metadata",
            "duration seconds",
            "seo",
            "retention",
        }
        key = re.sub(r"\s+", " ", str(parent_key or "").replace("_", " ")).strip().lower()
        if key in skip_keys:
            return []
        if isinstance(data, str):
            clean = data.strip()
            return [clean] if clean and key in keep_keys else []
        if isinstance(data, dict):
            values: list[str] = []
            for child_key, child_value in data.items():
                values.extend(PipelineWorker._collect_tts_json_values(child_value, str(child_key)))
            return values
        if isinstance(data, list):
            values: list[str] = []
            for item in data:
                values.extend(PipelineWorker._collect_tts_json_values(item, parent_key))
            return values
        return []

    @staticmethod
    def _normalize_tts_safe_text(text: str) -> str:
        value = str(text or "")
        replacements = {
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2013": "-",
            "\u2014": "-",
            "\u2026": "...",
            "\u2192": " to ",
            "\u2022": " ",
            "\ufe0f": "",
        }
        for old, new in replacements.items():
            value = value.replace(old, new)
        value = re.sub(r"[\U0001F300-\U0001FAFF]", " ", value)
        cue_words = r"pause|sigh|music|beat|angry|whisper|cry|laugh|breath|silence|cut"
        value = re.sub(rf"\[(?:{cue_words})[^\]]*\]", " ", value, flags=re.IGNORECASE)
        value = re.sub(rf"\((?:{cue_words})[^)]*\)", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*[-=]{2,}\s*", ". ", value)
        value = re.sub(r"[`*_#<>[\]{}]", " ", value)
        value = re.sub(r"\s+([,.!?;:])", r"\1", value)
        value = re.sub(r"[ \t]{2,}", " ", value)
        return value.strip()

    @staticmethod
    def _smooth_voice_paragraphs(text: str) -> str:
        paragraphs = [re.sub(r"\s*\n\s*", " ", item).strip() for item in re.split(r"\n\s*\n", str(text or ""))]
        paragraphs = [item for item in paragraphs if item]
        if not paragraphs:
            return ""

        smoothed: list[str] = []
        buffer: list[str] = []
        buffer_chars = 0
        buffer_sentences = 0

        def sentence_count(value: str) -> int:
            count = len(re.findall(r"[.!?][\"')\]]?(?:\s|$)", value))
            return max(1, count)

        def flush() -> None:
            nonlocal buffer, buffer_chars, buffer_sentences
            if buffer:
                smoothed.append(" ".join(buffer).strip())
                buffer = []
                buffer_chars = 0
                buffer_sentences = 0

        for paragraph in paragraphs:
            paragraph_sentences = sentence_count(paragraph)
            paragraph_chars = len(paragraph)
            if paragraph_chars >= 520 or paragraph_sentences >= 4:
                flush()
                smoothed.append(paragraph)
                continue
            if buffer and (buffer_sentences >= 3 or buffer_chars + paragraph_chars > 620):
                flush()
            buffer.append(paragraph)
            buffer_chars += paragraph_chars + 1
            buffer_sentences += paragraph_sentences
            if buffer_sentences >= 4 or buffer_chars >= 360:
                flush()
        flush()
        return "\n\n".join(smoothed)

    def _render(self, template, chapter_count: int, chapter_index: int = 0, artifacts: dict | None = None, extra: dict | None = None) -> str:
        text = "\n".join(str(x) for x in template) if isinstance(template, list) else str(template or "")
        artifacts = artifacts or {}
        extra = extra or {}
        chapter_word_count = self._chapter_word_count(chapter_count)
        values = {
            "chapter.count": str(chapter_count),
            "chapter.index": str(chapter_index),
            "computed.chapter_word_count": str(chapter_word_count),
            "computed.target_word_count": str(self.input_data.get("target_word_count") or ""),
        }
        for key, value in extra.items():
            values[str(key)] = str(value or "")
        for key, value in self.input_data.items():
            values[f"input.{key}"] = str(value or "")
        for key, value in artifacts.items():
            values[f"artifact.{key}"] = str(value or "")

        def replace(match):
            key = match.group(1).strip()
            return values.get(key, f"[MISSING: {key}]")

        return re.sub(r"{{\s*([^}]+?)\s*}}", replace, text).strip() + "\n"

    def _chapter_word_count(self, chapter_count: int) -> int:
        try:
            total = int(str(self.input_data.get("target_word_count") or "6800").replace(",", "").strip())
        except Exception:
            total = 6800
        return int(max(350, math.ceil(total / max(1, chapter_count) / 50) * 50))

    @staticmethod
    def _append_local_recap(previous: str, index: int, chapter_text: str) -> str:
        text = " ".join(str(chapter_text or "").split())
        if len(text) > 1400:
            text = f"{text[:760]} ... {text[-560:]}"
        block = f"Chapter {index:02d} recap: {text}"
        return (str(previous or "").strip() + "\n\n" + block).strip()
