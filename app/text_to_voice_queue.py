from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen


LANGUAGES = {
    "a": "American English",
    "b": "British English",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Brazilian Portuguese",
    "z": "Mandarin",
}

VOICES = {
    "a": [
        "af_heart",
        "af_alloy",
        "af_aoede",
        "af_bella",
        "af_jessica",
        "af_kore",
        "af_nicole",
        "af_nova",
        "af_river",
        "af_sarah",
        "af_sky",
        "am_adam",
        "am_echo",
        "am_eric",
        "am_fenrir",
        "am_liam",
        "am_michael",
        "am_onyx",
        "am_puck",
        "am_santa",
    ],
    "b": ["bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis"],
    "e": ["ef_dora", "em_alex", "em_santa"],
    "f": ["ff_siwis"],
    "h": ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"],
    "i": ["if_sara", "im_nicola"],
    "j": ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"],
    "p": ["pf_dora", "pm_alex", "pm_santa"],
    "z": ["zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang"],
}

DELIVERY_STYLES = {
    "plain": "Mac dinh",
    "natural": "Tu nhien",
    "expressive": "Nhan nhe",
    "dramatic": "Dien cam",
    "heavy_drama": "Heavy Drama",
    "storytelling": "Ke chuyen",
    "calm": "Diem tinh",
}


def _win_hidden_kwargs() -> dict:
    if os.name != "nt":
        return {}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {"startupinfo": si, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    except Exception:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def text_to_voice_root(settings: dict) -> Path:
    raw = str(settings.get("text_to_voice_root") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return path
    return Path(__file__).resolve().parents[1] / "kokoro-tts-local"


def text_to_voice_python(settings: dict, root: Path | None = None) -> Path:
    raw = str(settings.get("text_to_voice_python") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return path
    root = root or text_to_voice_root(settings)
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def validate_text_to_voice(settings: dict) -> tuple[Path, Path]:
    root = text_to_voice_root(settings)
    python = text_to_voice_python(settings, root)
    if not root.exists():
        raise FileNotFoundError(f"Khong thay thu muc Text to Voice: {root}")
    if not (root / "app.py").exists():
        raise FileNotFoundError(f"Khong thay app.py trong Text to Voice: {root}")
    if not python.exists():
        raise FileNotFoundError(
            f"Khong thay Python venv cua Text to Voice: {python}. Hay chay setup.ps1 trong thu muc kokoro-tts-local."
        )
    return root, python


def text_to_voice_url(settings: dict) -> str:
    host = str(settings.get("text_to_voice_host") or "127.0.0.1")
    port = int(settings.get("text_to_voice_port") or 7860)
    return f"http://{host}:{port}"


def is_text_to_voice_server_ready(settings: dict) -> bool:
    try:
        with urlopen(f"{text_to_voice_url(settings)}/api/config", timeout=1.5) as response:
            return int(response.status or 0) == 200
    except (URLError, OSError, ValueError):
        return False


def wait_for_text_to_voice_server(settings: dict, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + int(timeout_seconds)
    while time.time() < deadline:
        if is_text_to_voice_server_ready(settings):
            return True
        time.sleep(0.4)
    return False


def text_to_voice_parallel_jobs(settings: dict, fallback: int = 8) -> int:
    raw = settings.get("text_to_voice_parallel_jobs")
    if raw in (None, ""):
        raw = fallback
    try:
        count = int(str(raw).strip())
    except Exception:
        count = int(fallback or 8)
    return max(1, min(count, 20))


def ensure_text_to_voice_server(settings: dict, log: Callable[[str], None] | None = None) -> str:
    root, python = validate_text_to_voice(settings)
    url = text_to_voice_url(settings)
    if is_text_to_voice_server_ready(settings):
        if callable(log):
            log(f"Text to Voice UI da san sang: {url}")
        return url

    log_path = root / "ui-server.log"
    err_path = root / "ui-server.err.log"
    cmd = [
        str(python),
        str(root / "app.py"),
        "--host",
        str(settings.get("text_to_voice_host") or "127.0.0.1"),
        "--port",
        str(int(settings.get("text_to_voice_port") or 7860)),
    ]
    if callable(log):
        log(f"Khoi dong Text to Voice UI: {url}")
    with log_path.open("a", encoding="utf-8") as stdout, err_path.open("a", encoding="utf-8") as stderr:
        subprocess.Popen(cmd, cwd=str(root), stdout=stdout, stderr=stderr, **_win_hidden_kwargs())

    if not wait_for_text_to_voice_server(settings, timeout_seconds=45):
        raise RuntimeError(f"Text to Voice UI chua san sang o {url}. Xem log: {err_path}")
    return url


class TextToVoiceRunner:
    def __init__(self, settings: dict, log: Callable[[str], None], stop_check: Callable[[], bool]):
        self.settings = settings
        self.log = log
        self.stop_check = stop_check
        self.root: Path | None = None
        self.python: Path | None = None

    def start(self) -> None:
        self.root, self.python = validate_text_to_voice(self.settings)
        self.log(f"Text to Voice local da san sang: {self.root}")

    def close(self) -> None:
        return None

    def submit_chapter(self, chapter_index: int, text_path: str, output_path: str) -> str:
        text_file = Path(text_path)
        text = text_file.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("Text chapter rong.")

        output = Path(output_path)
        if output.suffix.lower() != ".wav":
            output = output.with_suffix(".wav")
        output.parent.mkdir(parents=True, exist_ok=True)

        label = f"chapter_{int(chapter_index):02d}"
        self.log(f"Text to Voice {label}: tao audio ({len(text)} ky tu)")
        return self.submit_file(text_file, label, output)

    def submit_file(self, text_path: Path, label: str, output_path: Path) -> str:
        if self.root is None or self.python is None:
            raise RuntimeError("Text to Voice runner chua start.")
        if self.stop_check():
            raise RuntimeError("Stopped.")

        cache_key = self._cache_key(text_path)
        if self._can_reuse_output(output_path, cache_key):
            self.log(f"Text to Voice {label}: dung lai audio da co {output_path.name}")
            return str(output_path)

        cli_path = Path(__file__).with_name("text_to_voice_cli.py")
        cmd = [
            str(self.python),
            str(cli_path),
            "--ttv-root",
            str(self.root),
            "--input",
            str(text_path),
            "--out",
            str(output_path),
            "--lang",
            str(self.settings.get("text_to_voice_language") or "a"),
            "--voice",
            str(self.settings.get("text_to_voice_voice") or "af_heart"),
            "--speed",
            str(float(self.settings.get("text_to_voice_speed") or 1.0)),
            "--delivery",
            str(self.settings.get("text_to_voice_delivery") or "dramatic"),
            "--max-chars",
            str(int(self.settings.get("text_to_voice_max_chars") or 10000)),
        ]
        timeout_seconds = int(self.settings.get("text_to_voice_timeout") or 1800)
        stdout_path = output_path.with_suffix(".ttv.stdout.log")
        stderr_path = output_path.with_suffix(".ttv.stderr.log")
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            cmd,
            cwd=str(self.root),
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_win_hidden_kwargs(),
        )

        deadline = time.time() + timeout_seconds
        last_log = 0.0
        try:
            while process.poll() is None:
                if self.stop_check():
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise RuntimeError("Stopped.")
                if time.time() > deadline:
                    process.kill()
                    raise RuntimeError(f"Timeout tao Text to Voice: {label}")
                if time.time() - last_log >= 20:
                    self.log(f"Text to Voice {label}: dang tao audio...")
                    last_log = time.time()
                time.sleep(0.5)
        finally:
            stdout_file.close()
            stderr_file.close()

        stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        if process.returncode != 0:
            detail = (stderr or stdout or "").strip()
            raise RuntimeError(detail[-1400:] or f"Text to Voice failed voi exit code {process.returncode}")

        result = self._parse_result(stdout)
        final_path = Path(str(result.get("output") or output_path))
        if not final_path.exists():
            raise RuntimeError(f"Text to Voice khong tao file output: {final_path}")
        parts = int(result.get("parts") or 1)
        suffix = f" ({parts} phan)" if parts > 1 else ""
        self._write_cache_meta(final_path, cache_key)
        self.log(f"Text to Voice {label}: da luu audio {final_path.name}{suffix}")
        return str(final_path)

    @staticmethod
    def _parse_result(stdout: str) -> dict:
        for line in reversed(str(stdout or "").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
        return {}

    def _cache_key(self, text_path: Path) -> dict:
        stat = text_path.stat()
        return {
            "text_path": str(text_path.resolve()),
            "text_size": int(stat.st_size),
            "text_mtime_ns": int(stat.st_mtime_ns),
            "language": str(self.settings.get("text_to_voice_language") or "a"),
            "voice": str(self.settings.get("text_to_voice_voice") or "af_heart"),
            "speed": str(float(self.settings.get("text_to_voice_speed") or 1.0)),
            "delivery": str(self.settings.get("text_to_voice_delivery") or "dramatic"),
            "max_chars": str(int(self.settings.get("text_to_voice_max_chars") or 10000)),
            "segment_cleaner": "tts_clean_v5",
        }

    @staticmethod
    def _can_reuse_output(output_path: Path, cache_key: dict) -> bool:
        meta_path = output_path.with_suffix(".ttv.meta.json")
        timing_path = output_path.with_suffix(".segments.json")
        if (
            not output_path.exists()
            or output_path.stat().st_size <= 1024
            or not meta_path.exists()
            or not timing_path.exists()
        ):
            return False
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return data == cache_key
        except Exception:
            return False

    @staticmethod
    def _write_cache_meta(output_path: Path, cache_key: dict) -> None:
        try:
            output_path.with_suffix(".ttv.meta.json").write_text(
                json.dumps(cache_key, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


class TextToVoiceQueue:
    def __init__(
        self,
        settings: dict,
        log: Callable[[str], None],
        status: Callable[[int, str, str], None],
        max_workers: int | None = None,
    ):
        self.settings = settings
        self.log = log
        self.status = status
        self.max_workers = text_to_voice_parallel_jobs(settings, fallback=max_workers or 8)
        self.jobs: queue.Queue[dict | None] = queue.Queue()
        self.stop_requested = False
        self.finish_requested = False
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        if self.is_alive():
            return
        self.stop_requested = False
        self.finish_requested = False
        self.threads = []
        self.log(f"Text to Voice song song: {self.max_workers} voice worker")
        for index in range(1, self.max_workers + 1):
            thread = threading.Thread(
                target=self._run_worker,
                args=(index,),
                name=f"text-to-voice-worker-{index}",
                daemon=True,
            )
            self.threads.append(thread)
            thread.start()

    def enqueue(self, chapter_index: int, text_path: str, output_path: str) -> None:
        self.jobs.put({"chapter_index": int(chapter_index), "text_path": str(text_path), "output_path": str(output_path)})

    def finish_when_empty(self) -> None:
        if self.finish_requested:
            return
        self.finish_requested = True
        for _ in range(max(1, len(self.threads) or self.max_workers)):
            self.jobs.put(None)

    def stop(self) -> None:
        self.stop_requested = True
        for _ in range(max(1, len(self.threads) or self.max_workers)):
            self.jobs.put(None)

    def is_alive(self) -> bool:
        return any(thread.is_alive() for thread in self.threads)

    def _run_worker(self, worker_index: int) -> None:
        runner = TextToVoiceRunner(self.settings, log=self.log, stop_check=lambda: self.stop_requested)
        started = False
        try:
            while not self.stop_requested:
                job = self.jobs.get()
                if job is None:
                    break
                chapter_index = int(job.get("chapter_index") or 0)
                try:
                    if not started:
                        runner.start()
                        started = True
                    self.status(chapter_index, "running", f"Dang tao Text to Voice worker {worker_index}")
                    detail = runner.submit_chapter(
                        chapter_index,
                        str(job.get("text_path") or ""),
                        str(job.get("output_path") or ""),
                    )
                    self.status(chapter_index, "done", detail)
                except Exception as exc:
                    self.status(chapter_index, "error", str(exc))
                    self.log(f"Text to Voice loi chapter {chapter_index:02d}: {exc}")
        finally:
            if started:
                runner.close()
