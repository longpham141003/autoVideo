from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import wave
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".hf_cache"))
os.environ.setdefault("HF_HUB_CACHE", str(ROOT / ".hf_cache" / "hub"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))

from huggingface_hub import hf_hub_download
from huggingface_hub.utils import close_session

WEB_ROOT = ROOT / "web"
OUTPUTS_DIR = Path(os.environ.get("KOKORO_OUTPUTS_DIR") or ROOT / "outputs")
SAMPLE_RATE = 24000
REPO_ID = "hexgrad/Kokoro-82M"

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
    "plain": {
        "label": "Mặc định",
        "description": "Kokoro mặc định, ít can thiệp vào nhịp đọc.",
        "sentencePause": 0.08,
        "paragraphPause": 0.25,
        "speedBias": 1.0,
        "questionSpeed": 1.0,
        "exclaimSpeed": 1.0,
        "shortSpeed": 1.0,
    },
    "natural": {
        "label": "Tự nhiên",
        "description": "Nhịp đọc tự nhiên, có khoảng nghỉ sau câu và đoạn.",
        "sentencePause": 0.12,
        "paragraphPause": 0.28,
        "speedBias": 1.0,
        "questionSpeed": 0.98,
        "exclaimSpeed": 0.99,
        "shortSpeed": 0.98,
    },
    "expressive": {
        "label": "Nhấn nhá",
        "description": "Nhấn nhẹ hơn nhưng không kéo nghỉ quá dài giữa các ý.",
        "sentencePause": 0.16,
        "paragraphPause": 0.36,
        "speedBias": 1.0,
        "questionSpeed": 0.96,
        "exclaimSpeed": 0.98,
        "shortSpeed": 0.96,
    },
    "dramatic": {
        "label": "Diễn cảm",
        "description": "Kể chuyện có nhấn nhá hơn cho thoại, câu hỏi và các câu punchline ngắn.",
        "sentencePause": 0.15,
        "paragraphPause": 0.34,
        "speedBias": 1.0,
        "questionSpeed": 0.93,
        "exclaimSpeed": 0.96,
        "shortSpeed": 0.92,
        "dialogueSpeed": 0.94,
        "dialoguePause": 1.18,
        "punchlinePause": 1.35,
    },
    "heavy_drama": {
        "label": "Heavy Drama",
        "description": "Siêu nhấn nhá cho truyện drama: thoại chậm hơn, punchline nặng hơn, khoảng lặng rõ hơn.",
        "sentencePause": 0.22,
        "paragraphPause": 0.58,
        "speedBias": 0.98,
        "questionSpeed": 0.88,
        "exclaimSpeed": 0.9,
        "shortSpeed": 0.86,
        "dialogueSpeed": 0.84,
        "dialoguePause": 1.75,
        "punchlinePause": 1.9,
        "maxPause": 0.9,
    },
    "storytelling": {
        "label": "Kể chuyện",
        "description": "Hợp với đọc truyện, có nhịp nhưng không bị ngắt quãng dài.",
        "sentencePause": 0.18,
        "paragraphPause": 0.42,
        "speedBias": 0.99,
        "questionSpeed": 0.95,
        "exclaimSpeed": 0.98,
        "shortSpeed": 0.95,
    },
    "calm": {
        "label": "Điềm tĩnh",
        "description": "Đọc chậm, rõ, ít gấp.",
        "sentencePause": 0.38,
        "paragraphPause": 0.9,
        "speedBias": 0.88,
        "questionSpeed": 0.92,
        "exclaimSpeed": 0.93,
        "shortSpeed": 0.91,
    },
}

PIPELINES: dict[str, KPipeline] = {}
PIPELINE_LOCK = threading.Lock()
GENERATE_LOCK = threading.Lock()


def to_numpy(audio: object) -> np.ndarray:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype=np.float32)


def get_pipeline(lang: str) -> KPipeline:
    with PIPELINE_LOCK:
        if lang not in PIPELINES:
            from kokoro import KPipeline

            PIPELINES[lang] = KPipeline(lang_code=lang, repo_id=REPO_ID)
        return PIPELINES[lang]


def safe_stem(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text.strip().lower())
    words = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE)[:28].strip("-_")
    return words or "speech"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


QUOTE_CHARS = "\"'“”‘’"


def clean_tts_segment_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    value = value.translate(str.maketrans("", "", QUOTE_CHARS))
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip(" ,;:")
    if not re.search(r"[\w\d]", value, flags=re.UNICODE):
        return ""
    return value


def split_long_sentence(sentence: str, max_chars: int = 430) -> list[str]:
    sentence = sentence.strip()
    if len(sentence) <= max_chars:
        return [sentence]

    parts = re.split(r"(?<=[,;:])\s+", sentence)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        candidate = f"{current} {part}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_sentences(paragraph: str) -> list[str]:
    paragraph = re.sub(r"\s+", " ", paragraph).strip()
    if not paragraph:
        return []
    sentences = re.findall(r"[^.!?;:]+(?:[.!?;:]+[\"'“”‘’)\]]*)?|[^.!?;:]+$", paragraph)
    pieces: list[str] = []
    for sentence in sentences:
        pieces.extend(split_long_sentence(sentence))
    return [piece.strip() for piece in pieces if piece.strip()]


def segment_speed(text: str, base_speed: float, style: dict[str, float | str], is_dialogue: bool = False) -> float:
    multiplier = float(style["speedBias"])
    clean = text.strip()
    word_count = len(clean.split())
    if clean.endswith("?"):
        multiplier *= float(style["questionSpeed"])
    elif clean.endswith("!"):
        multiplier *= float(style["exclaimSpeed"])
    if is_dialogue:
        multiplier *= float(style.get("dialogueSpeed", 0.97))
    if word_count <= 6:
        multiplier *= float(style["shortSpeed"])
    return clamp(base_speed * multiplier, 0.5, 2.0)


def dialogue_quote_count(text: str) -> int:
    return sum(str(text or "").count(mark) for mark in ('"', "“", "”"))


def sentence_pause(text: str, base_pause: float, style: dict[str, float | str], is_dialogue: bool) -> float:
    clean = text.strip()
    pause = float(base_pause)
    if is_dialogue:
        pause *= float(style.get("dialoguePause", 1.0))
    if len(clean.split()) <= 4:
        pause *= float(style.get("punchlinePause", 1.0))
    if clean.endswith("?"):
        pause *= 1.12
    return min(pause, float(style.get("maxPause", 0.55)))


def build_delivery_segments(text: str, base_speed: float, delivery: str) -> list[tuple[str, float, float]]:
    style = DELIVERY_STYLES.get(delivery, DELIVERY_STYLES["natural"])
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text.strip()) if part.strip()]
    if not paragraphs:
        return []

    segments: list[tuple[str, float, float]] = []
    for paragraph in paragraphs:
        sentences = split_sentences(paragraph)
        if not sentences:
            continue
        inside_dialogue = False
        for index, sentence in enumerate(sentences):
            quote_count = dialogue_quote_count(sentence)
            segment_text = clean_tts_segment_text(sentence)
            if not segment_text:
                continue
            is_dialogue = inside_dialogue or quote_count > 0
            is_last_sentence = index == len(sentences) - 1
            base_pause = float(style["paragraphPause"] if is_last_sentence else style["sentencePause"])
            pause = sentence_pause(segment_text, base_pause, style, is_dialogue)
            segments.append((segment_text, pause, segment_speed(segment_text, base_speed, style, is_dialogue)))
            if quote_count % 2 == 1:
                inside_dialogue = not inside_dialogue

    if segments:
        last_text, _, last_speed = segments[-1]
        segments[-1] = (last_text, 0.0, last_speed)
    return segments


def audio_url(name: str) -> str:
    return f"/audio/{name}"


def list_outputs(limit: int = 24, include_previews: bool = False) -> list[dict[str, object]]:
    OUTPUTS_DIR.mkdir(exist_ok=True)
    files = sorted(OUTPUTS_DIR.glob("*.wav"), key=lambda item: item.stat().st_mtime, reverse=True)
    items = []
    for path in files:
        is_preview = "-preview-" in path.name
        if is_preview and not include_previews:
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
                "url": audio_url(path.name),
                "kind": "preview" if is_preview else "full",
            }
        )
        if len(items) >= limit:
            break
    return items


def resolve_voice_path(voice: str) -> str:
    if voice.endswith(".pt"):
        return voice

    try:
        close_session()
        return hf_hub_download(repo_id=REPO_ID, filename=f"voices/{voice}.pt")
    except Exception as exc:
        raise RuntimeError(
            f"Không tải được voice '{voice}'. Kiểm tra mạng hoặc chọn voice đã từng dùng."
        ) from exc


def synthesize(
    text: str,
    lang: str,
    voice: str,
    speed: float,
    prefix: str = "",
    delivery: str = "dramatic",
) -> dict[str, object]:
    if not text.strip():
        raise ValueError("Nhập nội dung cần tạo giọng.")
    if len(text) > 12000:
        raise ValueError("Nội dung quá dài. Hãy cắt thành đoạn ngắn hơn 12.000 ký tự.")
    if lang.lower() in {"vi", "vn", "vietnamese"}:
        raise ValueError("Kokoro hiện chưa có giọng tiếng Việt native. Hãy chọn ngôn ngữ khác.")
    if lang not in LANGUAGES:
        raise ValueError("Ngôn ngữ không hợp lệ.")
    if voice not in VOICES.get(lang, []):
        raise ValueError("Voice không hợp lệ với ngôn ngữ đang chọn.")
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Tốc độ phải nằm trong khoảng 0.5 đến 2.0.")
    if delivery not in DELIVERY_STYLES:
        raise ValueError("Kiểu đọc không hợp lệ.")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    kind = "preview" if prefix == "preview" else "full"
    stem_prefix = f"{kind}-"
    out_path = OUTPUTS_DIR / f"{timestamp}-{stem_prefix}{voice}-{safe_stem(text)}.wav"

    with GENERATE_LOCK:
        voice_path = resolve_voice_path(voice)
        pipeline = get_pipeline(lang)
        chunks: list[np.ndarray] = []
        timing_segments: list[dict[str, object]] = []
        cursor = 0.0
        segments = build_delivery_segments(text, speed, delivery)
        for segment_text, pause_seconds, segment_read_speed in segments:
            start = cursor
            speech_samples = 0
            for _, _, audio in pipeline(segment_text, voice=voice_path, speed=segment_read_speed, split_pattern=None):
                audio_array = to_numpy(audio)
                chunks.append(audio_array)
                speech_samples += int(len(audio_array))
                cursor += len(audio_array) / SAMPLE_RATE
            speech_end = cursor
            if pause_seconds > 0:
                pause_samples = int(SAMPLE_RATE * pause_seconds)
                chunks.append(np.zeros(pause_samples, dtype=np.float32))
                cursor += pause_samples / SAMPLE_RATE
            timing_segments.append(
                {
                    "text": segment_text,
                    "start": round(start, 4),
                    "end": round(speech_end, 4),
                    "pause": round(max(0.0, cursor - speech_end), 4),
                    "speed": round(segment_read_speed, 4),
                    "samples": speech_samples,
                }
            )

    if not chunks:
        raise RuntimeError("Kokoro không trả về audio.")

    final_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    tmp_dir = Path(os.environ.get("KOKORO_TMP_OUTPUT_DIR") or tempfile.gettempdir())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"kokoro-{timestamp}-{os.getpid()}-{threading.get_ident()}.wav"
    audio_to_write = np.asarray(final_audio, dtype=np.float32)
    audio_to_write = np.clip(audio_to_write, -1.0, 1.0)
    pcm_audio = (audio_to_write * 32767.0).astype(np.int16)
    with wave.open(str(tmp_path), "wb") as wav_file:
        channels = 1 if pcm_audio.ndim == 1 else int(pcm_audio.shape[1])
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_audio.tobytes())
    last_exc = None
    for _attempt in range(8):
        try:
            if out_path.exists():
                out_path.unlink()
            shutil.copyfile(str(tmp_path), str(out_path))
            tmp_path.unlink(missing_ok=True)
            break
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.35)
    else:
        raise last_exc or RuntimeError(f"Khong ghi duoc audio output: {out_path}")
    duration = len(final_audio) / SAMPLE_RATE

    return {
        "name": out_path.name,
        "path": str(out_path),
        "url": audio_url(out_path.name),
        "duration": round(duration, 2),
        "sampleRate": SAMPLE_RATE,
        "size": out_path.stat().st_size,
        "kind": kind,
        "delivery": delivery,
        "segments": timing_segments,
        "outputs": list_outputs(),
    }


class KokoroHandler(SimpleHTTPRequestHandler):
    server_version = "KokoroLocalUI/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json(
                {
                    "languages": LANGUAGES,
                    "voices": VOICES,
                    "deliveryStyles": DELIVERY_STYLES,
                    "outputs": list_outputs(),
                }
            )
            return
        if parsed.path.startswith("/audio/"):
            self.serve_audio(parsed.path.removeprefix("/audio/"))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/generate":
            self.send_error_json("Endpoint không tồn tại.", HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            result = synthesize(
                text=str(data.get("text", "")),
                lang=str(data.get("lang", "a")),
                voice=str(data.get("voice", "af_heart")),
                speed=float(data.get("speed", 1.0)),
                prefix=str(data.get("prefix", "")),
                delivery=str(data.get("delivery", "dramatic")),
            )
        except Exception as exc:
            self.send_error_json(str(exc))
            return

        self.send_json(result)

    def serve_static(self, request_path: str) -> None:
        path = WEB_ROOT / ("index.html" if request_path in {"", "/"} else request_path.lstrip("/"))
        try:
            resolved = path.resolve()
            resolved.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if not resolved.exists() or not resolved.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type = f"{content_type}; charset=utf-8"
        body = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_audio(self, file_name: str) -> None:
        safe_name = Path(unquote(file_name)).name
        path = OUTPUTS_DIR / safe_name
        if not path.exists() or path.suffix.lower() != ".wav":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run Kokoro TTS local web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), KokoroHandler)
    print(f"Kokoro TTS UI: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
