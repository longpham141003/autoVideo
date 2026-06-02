from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
import unicodedata
from pathlib import Path


os.environ.setdefault("COQUI_TOS_AGREED", "1")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"
LOCAL_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "AIContentVoiceStudio"
SAFE_SAMPLE_DIR = LOCAL_DATA_DIR / "samples"
SAFE_OUTPUT_DIR = LOCAL_DATA_DIR / "outputs"

STYLE_SETTINGS = {
    "Balanced": {"temperature": 0.65, "top_p": 0.80, "top_k": 45, "repetition_penalty": 10.0},
    "Clear stable": {"temperature": 0.55, "top_p": 0.70, "top_k": 35, "repetition_penalty": 12.0},
    "Expressive": {"temperature": 0.78, "top_p": 0.88, "top_k": 60, "repetition_penalty": 9.0},
}

QUOTE_CHARS = "\"'“”‘’"


class VoiceServer:
    def __init__(self):
        self.tts = None

    def emit(self, event: str, **payload) -> None:
        payload["event"] = event
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    def load_model(self, job_id: str) -> None:
        if self.tts is not None:
            return
        self.emit("log", job_id=job_id, message="Dang tai Coqui XTTS v2...")
        from TTS.api import TTS

        try:
            import torch

            torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "4")))
        except Exception:
            pass

        self.tts = TTS(XTTS_MODEL, gpu=False)
        try:
            self.tts.synthesizer.tts_config.audio["do_trim_silence"] = False
        except Exception:
            pass
        self.emit("log", job_id=job_id, message="XTTS da san sang.")

    def process_job(self, job: dict) -> None:
        job_id = str(job.get("job_id") or "voice")
        try:
            self.load_model(job_id)
            text_path = Path(str(job.get("text_path") or ""))
            output_path = Path(str(job.get("output_path") or ""))
            sample_path = Path(str(job.get("speaker_wav") or ""))
            style_name = str(job.get("style") or "Clear stable")
            speed = float(job.get("speed") or 1.0)
            language = str(job.get("language") or "en")

            if not text_path.exists():
                raise FileNotFoundError(f"Khong thay text file: {text_path}")
            text = text_path.read_text(encoding="utf-8").strip()
            if not text:
                raise ValueError("Text rong, khong tao voice.")
            if not sample_path.exists():
                raise FileNotFoundError(f"Khong thay file mau giong: {sample_path}")

            settings = dict(STYLE_SETTINGS.get(style_name, STYLE_SETTINGS["Clear stable"]))
            settings["speed"] = speed
            speaker_wav = self.prepare_speaker_sample(sample_path, job_id)
            safe_out = self.prepare_safe_output(output_path)

            sentences = self.tts.synthesizer.split_into_sentences(text)
            wav = []
            self.emit("log", job_id=job_id, message=f"Bat dau tao voice: {len(sentences)} cau")
            for index, sentence in enumerate(sentences, start=1):
                sentence = clean_voice_sentence(sentence)
                if not sentence:
                    continue
                self.emit("progress", job_id=job_id, current=index, total=len(sentences), message=f"Cau {index}/{len(sentences)}")
                common = {
                    "text": sentence,
                    "config": self.tts.synthesizer.tts_config,
                    "language": language,
                    "voice_dirs": None,
                    "d_vector": None,
                    **settings,
                }
                outputs = self.tts.synthesizer.tts_model.synthesize(
                    speaker_wav=str(speaker_wav),
                    speaker_id=None,
                    **common,
                )
                wav.extend(outputs["wav"])
                wav.extend([0.0] * int(job.get("pause_samples") or 9000))

            self.tts.synthesizer.save_wav(wav=wav, path=str(safe_out))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_out, output_path)
            self.emit("done", job_id=job_id, output_path=str(output_path), message=f"Da luu voice: {output_path.name}")
        except Exception as exc:
            self.emit("error", job_id=job_id, message=str(exc), traceback=traceback.format_exc()[-1800:])

    def prepare_speaker_sample(self, path: Path, job_id: str) -> Path:
        if path.suffix.lower() == ".wav" and is_ascii_path(path):
            return path
        SAFE_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = f"{ascii_slug(path.stem, 'voice_sample')[:72]}{path.suffix.lower() or '.wav'}"
        safe_path = SAFE_SAMPLE_DIR / safe_name
        if not safe_path.exists() or safe_path.stat().st_size != path.stat().st_size:
            shutil.copy2(path, safe_path)
            self.emit("log", job_id=job_id, message=f"Copy mau giong sang path an toan: {safe_path}")
        return safe_path

    def prepare_safe_output(self, output_path: Path) -> Path:
        SAFE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return SAFE_OUTPUT_DIR / f"{ascii_slug(output_path.stem, 'voice_output')[:72]}.wav"


def ascii_slug(value: str, fallback: str = "item") -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in ascii_text).strip("_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe or fallback


def is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def clean_voice_sentence(text: str) -> str:
    value = " ".join(str(text or "").split())
    value = value.translate(str.maketrans("", "", QUOTE_CHARS))
    value = value.strip(" ,;:")
    if not any(ch.isalnum() for ch in value):
        return ""
    return value


def main() -> int:
    server = VoiceServer()
    server.emit("ready", message="Voice worker ready")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        if line == "__quit__":
            server.emit("bye", message="Voice worker stopped")
            return 0
        try:
            job = json.loads(line)
        except Exception as exc:
            server.emit("error", job_id="unknown", message=f"Bad job JSON: {exc}")
            continue
        if isinstance(job, dict):
            server.process_job(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
