from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".hf_cache"))
os.environ.setdefault("HF_HUB_CACHE", str(ROOT / ".hf_cache" / "hub"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))

import numpy as np
import soundfile as sf
from kokoro import KPipeline


SAMPLE_RATE = 24000

LANG_HELP = (
    "Official example codes: a=American English, b=British English, "
    "e=Spanish, f=French, h=Hindi, i=Italian, j=Japanese, "
    "p=Brazilian Portuguese, z=Mandarin."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a local speech file with Kokoro TTS.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Text to synthesize.")
    input_group.add_argument("--file", type=Path, help="UTF-8 text file to synthesize.")
    parser.add_argument("--out", type=Path, default=Path("outputs/speech.wav"), help="Output audio file path.")
    parser.add_argument("--lang", default="a", help=LANG_HELP)
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice name or path to a voice tensor.")
    parser.add_argument("--repo-id", default="hexgrad/Kokoro-82M", help="Hugging Face repo used by Kokoro.")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed multiplier.")
    parser.add_argument(
        "--split-pattern",
        default=r"\n+",
        help="Regex used by Kokoro to split long text into chunks.",
    )
    parser.add_argument(
        "--save-chunks",
        action="store_true",
        help="Also save each generated chunk next to the final output.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print chunk text and phonemes.")
    return parser.parse_args()


def read_input(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text.strip()
    try:
        return args.file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise SystemExit(f"Input file not found: {args.file}") from None


def to_numpy(audio: object) -> np.ndarray:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype=np.float32)


def save_audio(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, SAMPLE_RATE)


def main() -> int:
    args = parse_args()
    text = read_input(args)
    if not text:
        print("No input text provided.", file=sys.stderr)
        return 2

    if args.lang.lower() in {"vi", "vn", "vietnamese"}:
        print(
            "Kokoro does not currently provide native Vietnamese support. "
            "Use one of the supported language codes instead.",
            file=sys.stderr,
        )
        return 2

    print(f"Loading Kokoro pipeline: lang={args.lang}, voice={args.voice}, repo={args.repo_id}")
    pipeline = KPipeline(lang_code=args.lang, repo_id=args.repo_id)

    chunks: list[np.ndarray] = []
    generator = pipeline(
        text,
        voice=args.voice,
        speed=args.speed,
        split_pattern=args.split_pattern,
    )

    for index, (graphemes, phonemes, audio) in enumerate(generator):
        audio_np = to_numpy(audio)
        chunks.append(audio_np)

        if args.verbose:
            print(f"\nChunk {index}")
            print(f"Text: {graphemes}")
            print(f"Phonemes: {phonemes}")
        else:
            print(f"Generated chunk {index}: {len(audio_np) / SAMPLE_RATE:.2f}s")

        if args.save_chunks:
            chunk_path = args.out.with_name(f"{args.out.stem}_{index:03d}{args.out.suffix}")
            save_audio(chunk_path, audio_np)
            print(f"Saved chunk: {chunk_path}")

    if not chunks:
        print("Kokoro returned no audio chunks.", file=sys.stderr)
        return 1

    final_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    save_audio(args.out, final_audio)
    print(f"Saved: {args.out} ({len(final_audio) / SAMPLE_RATE:.2f}s, {SAMPLE_RATE} Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
