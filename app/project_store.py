from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from .config import rebase_app_path


PRIMARY_ARTIFACT_LABELS = {
    "new_title_en": "Tiêu đề mới",
    "new_thumb_en": "Text thumbnail mới",
    "title_thumb_variants": "Bảng chấm tiêu đề & thumbnail",
    "opening_hook_lab": "Opening hook",
    "retention_map": "Bản đồ giữ chân người xem",
    "youtube_description": "Mô tả YouTube",
    "upload_package": "Gói đăng YouTube",
    "veo3_prompts": "Prompt ảnh/video",
    "shorts_package": "Gói Shorts",
}

TECHNICAL_ARTIFACT_LABELS = {
    "original_thumb_text": "Text thumbnail gốc",
    "source_script_analysis": "Phân tích kịch bản gốc",
    "new_story_summary": "Tóm tắt chuyện mới",
    "compact_new_story_summary": "Tóm tắt rút gọn",
    "original_summary": "Tóm tắt kịch bản gốc",
    "seo_keywords": "Từ khóa SEO",
    "community_poll": "Câu hỏi cộng đồng",
    "character_bible": "Hồ sơ nhân vật",
    "character_reference_prompts": "Prompt ảnh reference nhân vật",
}


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def file_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def slugify(value: str, fallback: str = "project") -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-+", "-", text)
    return (text[:64].strip("-") or fallback)


class ProjectStore:
    def __init__(self, projects_dir: str | Path):
        self.projects_dir = Path(projects_dir)
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def create_project(self, input_data: dict) -> Path:
        title = str(input_data.get("original_title") or "story").strip()
        project_id = f"{file_stamp()}_{slugify(title)}"
        project_dir = self.projects_dir / project_id
        for name in ("artifacts", "scripts", "voices", "logs", "voice_jobs", "veo_videos", "final", "assets", "shorts"):
            (project_dir / name).mkdir(parents=True, exist_ok=True)

        input_data = dict(input_data or {})
        character_path = Path(str(input_data.get("character_image_path") or ""))
        if character_path.exists() and character_path.is_file():
            target = project_dir / "assets" / f"character{character_path.suffix.lower() or '.png'}"
            try:
                shutil.copy2(character_path, target)
                input_data["character_image_path"] = str(target)
            except Exception:
                pass

        self.write_json(project_dir / "input.json", input_data)
        self.write_json(
            project_dir / "state.json",
            {
                "project_id": project_id,
                "status": "created",
                "created_at": now_stamp(),
                "updated_at": now_stamp(),
                "steps": {},
                "chapters": {},
            },
        )
        (project_dir / "logs" / "run.log").write_text("", encoding="utf-8")
        return project_dir

    def load_input(self, project_dir: Path | str) -> dict:
        project_dir = Path(project_dir)
        try:
            data = json.loads((project_dir / "input.json").read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return self.normalize_input_paths(data, project_dir)
        except Exception:
            pass
        return {}

    def normalize_input_paths(self, input_data: dict, project_dir: Path | str) -> dict:
        data = dict(input_data or {})
        project_dir = Path(project_dir)
        for key in ("thumbnail_path", "character_image_path", "character_path", "final_video_character_path"):
            raw = str(data.get(key) or "").strip()
            if not raw:
                continue
            rebased = rebase_app_path(raw, require_exists=True)
            path = Path(rebased)
            if not path.exists() and key in {"character_image_path", "character_path", "final_video_character_path"}:
                name = Path(raw).name
                for candidate in (
                    project_dir / "assets" / name,
                    project_dir / "assets" / "character.png",
                    project_dir / "assets" / "character.webp",
                    project_dir / "assets" / "character.jpg",
                    project_dir / "assets" / "character.jpeg",
                ):
                    if candidate.exists():
                        rebased = str(candidate)
                        break
            data[key] = rebased
        return data

    def append_log(self, project_dir: Path, message: str) -> None:
        line = f"[{now_stamp()}] {message}\n"
        try:
            log_path = project_dir / "logs" / "run.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def state_path(self, project_dir: Path) -> Path:
        return project_dir / "state.json"

    def load_state(self, project_dir: Path) -> dict:
        try:
            data = json.loads(self.state_path(project_dir).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"steps": {}, "chapters": {}}

    def save_state(self, project_dir: Path, state: dict) -> None:
        state = state if isinstance(state, dict) else {}
        state["updated_at"] = now_stamp()
        self.write_json(self.state_path(project_dir), state)

    def update_step(self, project_dir: Path, step_id: str, status: str, **extra) -> None:
        state = self.load_state(project_dir)
        steps = state.setdefault("steps", {})
        item = steps.setdefault(str(step_id), {})
        item.update({"status": status, "updated_at": now_stamp(), **extra})
        self.save_state(project_dir, state)

    def update_chapter(self, project_dir: Path, chapter_index: int, **extra) -> None:
        state = self.load_state(project_dir)
        chapters = state.setdefault("chapters", {})
        key = f"{int(chapter_index):02d}"
        item = chapters.setdefault(key, {})
        item.update({"updated_at": now_stamp(), **extra})
        self.save_state(project_dir, state)

    def save_artifact(self, project_dir: Path, artifact_id: str, text: str) -> Path:
        path = project_dir / "artifacts" / f"{artifact_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or "").strip() + "\n", encoding="utf-8")
        self.update_step(project_dir, artifact_id, "done", path=str(path), chars=len(text or ""))
        return path

    def read_artifact(self, project_dir: Path, artifact_id: str) -> str:
        path = project_dir / "artifacts" / f"{artifact_id}.txt"
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def save_chapter_text(self, project_dir: Path, chapter_index: int, text: str) -> Path:
        path = project_dir / "scripts" / f"chapter_{int(chapter_index):02d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or "").strip() + "\n", encoding="utf-8")
        self.update_chapter(
            project_dir,
            chapter_index,
            text_status="done",
            text_path=str(path),
            chars=len(text or ""),
        )
        return path

    def expected_voice_path(self, project_dir: Path, chapter_index: int) -> Path:
        return project_dir / "voices" / f"chapter_{int(chapter_index):02d}.wav"

    def existing_voice_path(self, project_dir: Path, chapter_index: int) -> Path:
        expected = self.expected_voice_path(project_dir, chapter_index)
        if expected.exists():
            return expected
        stem = f"chapter_{int(chapter_index):02d}"
        for suffix in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm"):
            path = project_dir / "voices" / f"{stem}{suffix}"
            if path.exists():
                return path
        part_files = sorted((project_dir / "voices").glob(f"{stem}_part_*.*"))
        if part_files:
            return project_dir / "voices"
        return expected

    def list_projects(self) -> list[Path]:
        if not self.projects_dir.exists():
            return []
        dirs = [p for p in self.projects_dir.iterdir() if p.is_dir()]
        return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)

    def collect_results(self, project_dir: Path) -> list[dict]:
        rows: list[dict] = []
        scripts = sorted((project_dir / "scripts").glob("chapter_*.txt"))
        for script in scripts:
            match = re.search(r"chapter_(\d+)", script.stem)
            index = int(match.group(1)) if match else len(rows) + 1
            voice = self.existing_voice_path(project_dir, index)
            rows.append(
                {
                    "type": "chapter",
                    "name": f"Chapter {index:02d}",
                    "index": index,
                    "text_path": str(script),
                    "voice_path": str(voice) if voice.exists() else "",
                }
            )

        artifacts = {path.stem: path for path in sorted((project_dir / "artifacts").glob("*.txt"))}
        for stem, artifact in artifacts.items():
            if stem not in PRIMARY_ARTIFACT_LABELS:
                continue
            rows.append(
                {
                    "type": "chính",
                    "name": PRIMARY_ARTIFACT_LABELS[stem],
                    "index": 0,
                    "text_path": str(artifact),
                    "voice_path": "",
                }
            )

        for stem, artifact in artifacts.items():
            if (
                stem in PRIMARY_ARTIFACT_LABELS
                or stem not in TECHNICAL_ARTIFACT_LABELS
                or stem == "previous_chapters_summary"
                or stem.startswith("chapter_")
                or stem.startswith("script_qa_")
                or ".part" in stem
            ):
                continue
            rows.append(
                {
                    "type": "kỹ thuật",
                    "name": TECHNICAL_ARTIFACT_LABELS[stem],
                    "index": 0,
                    "text_path": str(artifact),
                    "voice_path": "",
                }
            )
        return rows

    @staticmethod
    def write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
