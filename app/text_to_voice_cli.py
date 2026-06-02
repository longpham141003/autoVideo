from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate speech with the latest local Text to Voice app.")
    parser.add_argument("--ttv-root", type=Path, required=True, help="Path to kokoro-tts-local.")
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 text file to synthesize.")
    parser.add_argument("--out", type=Path, required=True, help="Output WAV path.")
    parser.add_argument("--lang", default="a")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--delivery", default="dramatic")
    parser.add_argument("--max-chars", type=int, default=10000)
    return parser.parse_args()


def load_kokoro_app(ttv_root: Path):
    app_path = ttv_root / "app.py"
    if not app_path.exists():
        raise FileNotFoundError(f"Khong thay app.py trong Text to Voice root: {ttv_root}")
    spec = importlib.util.spec_from_file_location("kokoro_local_text_to_voice", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Khong load duoc Text to Voice app: {app_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare_espeak_runtime() -> None:
    if os.name != "nt":
        return
    try:
        import espeakng_loader
    except Exception:
        return

    cache_root = Path(os.environ.get("KOKORO_ESPEAK_CACHE") or Path(__file__).resolve().parents[1] / ".kokoro_espeakng_loader")
    data_src = Path(espeakng_loader.get_data_path())
    lib_src = Path(espeakng_loader.get_library_path())
    data_dst = cache_root / "espeak-ng-data"
    lib_dst = cache_root / lib_src.name

    cache_root.mkdir(parents=True, exist_ok=True)
    if not (data_dst / "phontab").exists():
        if data_dst.exists():
            shutil.rmtree(data_dst)
        shutil.copytree(data_src, data_dst)
    if not lib_dst.exists() or lib_dst.stat().st_size != lib_src.stat().st_size:
        shutil.copy2(lib_src, lib_dst)

    espeakng_loader.get_data_path = lambda: str(data_dst)
    espeakng_loader.get_library_path = lambda: str(lib_dst)
    os.environ["PHONEMIZER_ESPEAK_DATA_PATH"] = str(data_dst)
    os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = str(lib_dst)


def split_text_for_text_to_voice(text: str, max_chars: int) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []

    max_chars = max(1000, min(int(max_chars or 10000), 12000))
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

    pieces = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n\s*\n", source) if item.strip()]
    for piece in pieces:
        if len(piece) > max_chars:
            flush()
            words = piece.split()
            block: list[str] = []
            block_len = 0
            for word in words:
                add = len(word) + (1 if block else 0)
                if block and block_len + add > max_chars:
                    chunks.append(" ".join(block))
                    block = [word]
                    block_len = len(word)
                else:
                    block.append(word)
                    block_len += add
            if block:
                chunks.append(" ".join(block))
            continue

        add = len(piece) + (2 if current else 0)
        if current and current_len + add > max_chars:
            flush()
        current.append(piece)
        current_len += add

    flush()
    return chunks


def combine_wavs(paths: list[Path], output_path: Path) -> float:
    import numpy as np
    import soundfile as sf

    arrays = []
    sample_rate: int | None = None
    duration = 0.0
    for index, path in enumerate(paths):
        audio, sr = sf.read(path, dtype="float32", always_2d=False)
        if sample_rate is None:
            sample_rate = int(sr)
        elif int(sr) != sample_rate:
            raise RuntimeError(f"Sample rate khong khop: {path}")
        arrays.append(audio)
        duration += len(audio) / sample_rate
        if index < len(paths) - 1:
            pause_samples = int(sample_rate * 0.25)
            if getattr(audio, "ndim", 1) == 2:
                arrays.append(np.zeros((pause_samples, audio.shape[1]), dtype=np.float32))
            else:
                arrays.append(np.zeros(pause_samples, dtype=np.float32))
            duration += pause_samples / sample_rate

    if sample_rate is None or not arrays:
        raise RuntimeError("Khong co audio de ghep.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, np.concatenate(arrays), sample_rate)
    return duration


def shift_segment_timing(segment: dict, offset: float) -> dict:
    shifted = dict(segment)
    for key in ("start", "end"):
        try:
            shifted[key] = round(float(shifted.get(key) or 0.0) + offset, 4)
        except Exception:
            shifted[key] = round(offset, 4)
    return shifted


def is_voice_segment_text(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" \"'“”‘’.,;:!?")
    return bool(re.search(r"[\w\d]", value, flags=re.UNICODE))


def extract_tts_text_from_json_payload(text: str) -> str:
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
        values = collect_tts_json_values(data)
        if values:
            return "\n\n".join(values)
    return ""


def collect_tts_json_values(data: object, parent_key: str = "") -> list[str]:
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
            values.extend(collect_tts_json_values(child_value, str(child_key)))
        return values
    if isinstance(data, list):
        values: list[str] = []
        for item in data:
            values.extend(collect_tts_json_values(item, parent_key))
        return values
    return []


def sanitize_text_for_tts(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    json_text = extract_tts_text_from_json_payload(value)
    if json_text:
        value = json_text
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
    value = re.sub(r"```(?:text|plain|markdown|md|json)?\s*", "\n", value, flags=re.IGNORECASE)
    value = value.replace("```", "\n")

    cleaned_lines: list[str] = []
    skip_keys = {
        "image prompt",
        "image prompts",
        "veo prompt",
        "veo prompts",
        "video prompt",
        "video prompts",
        "negative prompt",
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
    cue_line = re.compile(rf"^\s*(?:{cue_words})(?:\b.*)?$", re.IGNORECASE)
    metadata_line = re.compile(r"^\s*[\"']?([A-Za-z][A-Za-z0-9_ -]{0,40})[\"']?\s*:\s*(.*?)[,;]?\s*$")
    for raw_line in value.split("\n"):
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if re.fullmatch(r"[{}\[\],]+", line):
            continue
        if cue_line.match(line.strip("[]() ")):
            continue
        match = metadata_line.match(line)
        if match:
            key = re.sub(r"\s+", " ", match.group(1).replace("_", " ")).strip().lower()
            raw_value = match.group(2).strip().strip(",").strip()
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"\"", "'"}:
                raw_value = raw_value[1:-1].strip()
            if key in skip_keys:
                continue
            if key in keep_value_keys:
                if raw_value:
                    line = raw_value
                else:
                    continue
        if re.match(r"^\s*#{1,6}\s+", line):
            continue
        if re.match(r"^\s*(?:---+|===+|\*\*\*+)\s*$", line):
            continue
        cleaned_lines.append(line)
    value = "\n".join(cleaned_lines)

    value = re.sub(r"\b(?:Emotional Goal|Mini-hook|Conflict|Visual|Action|Internal line|Immediate reaction)\s*:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:Image prompt|VEO prompt|Video prompt|Negative prompt|Caption overlay|Visual context)\s*:\s*[^.?!\n]{0,260}", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:Scene|Beat)\s+\d+\s*[-:]\s*[^.?!\n]{0,80}", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bDialogue\s*:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:Father|Mother|Sister|Brother|Assistant|Bridesmaid|Coordinator|Charlie|Olivia|Daniel|Noah|Samuel)\s*:\s*", "", value)
    value = re.sub(r"[`*_#<>[\]{}]", " ", value)
    value = re.sub(r"\s*[-=]{2,}\s*", ". ", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def main() -> int:
    args = parse_args()
    ttv_root = args.ttv_root.resolve()
    output_path = args.out
    if output_path.suffix.lower() != ".wav":
        output_path = output_path.with_suffix(".wav")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = sanitize_text_for_tts(args.input.read_text(encoding="utf-8"))
    chunks = split_text_for_text_to_voice(text, args.max_chars)
    if not chunks:
        raise ValueError("Text chapter rong.")

    kokoro_app = load_kokoro_app(ttv_root)
    prepare_espeak_runtime()
    generated_paths: list[Path] = []
    generated_results: list[dict] = []
    for chunk in chunks:
        result = kokoro_app.synthesize(
            text=chunk,
            lang=args.lang,
            voice=args.voice,
            speed=args.speed,
            delivery=args.delivery,
        )
        generated_results.append(dict(result))
        generated_paths.append(Path(str(result.get("path") or (ttv_root / "outputs" / str(result["name"])))))

    combined_segments: list[dict] = []
    offset = 0.0
    if len(generated_paths) == 1:
        shutil.copy2(generated_paths[0], output_path)
        duration = float(generated_results[0].get("duration") or 0.0)
        combined_segments = [
            shift_segment_timing(segment, 0.0)
            for segment in generated_results[0].get("segments", [])
            if isinstance(segment, dict) and is_voice_segment_text(str(segment.get("text") or ""))
        ]
    else:
        duration = combine_wavs(generated_paths, output_path)
        for index, result in enumerate(generated_results):
            for segment in result.get("segments", []):
                if isinstance(segment, dict) and is_voice_segment_text(str(segment.get("text") or "")):
                    combined_segments.append(shift_segment_timing(segment, offset))
            offset += float(result.get("duration") or 0.0)
            if index < len(generated_results) - 1:
                offset += 0.25

    timing_path = output_path.with_suffix(".segments.json")
    timing_path.write_text(
        json.dumps(
            {
                "audio": str(output_path),
                "duration": round(duration, 4),
                "sampleRate": int(generated_results[0].get("sampleRate") or 24000) if generated_results else 24000,
                "lang": args.lang,
                "voice": args.voice,
                "speed": args.speed,
                "delivery": args.delivery,
                "segments": combined_segments,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "output": str(output_path),
                "timing": str(timing_path),
                "parts": len(generated_paths),
                "lang": args.lang,
                "voice": args.voice,
                "speed": args.speed,
                "delivery": args.delivery,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
