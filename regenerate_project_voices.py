from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_settings
from app.text_to_voice_queue import TextToVoiceRunner
from app.video_editor import collect_script_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regenerate project voices and exact subtitle timing metadata.")
    parser.add_argument("project", type=Path, help="Project folder containing scripts/ and voices/.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project.resolve()
    if not project.exists():
        raise FileNotFoundError(f"Khong thay project: {project}")

    scripts = collect_script_files(project)
    if not scripts:
        raise FileNotFoundError(f"Khong thay scripts/chapter_*.txt trong: {project}")

    voices_dir = project / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    runner = TextToVoiceRunner(settings, log=print, stop_check=lambda: False)
    runner.start()
    try:
        for index, text_path in enumerate(scripts, start=1):
            output_path = voices_dir / f"chapter_{index:02d}.wav"
            print(f"Tao lai voice + timing chapter {index:02d}: {text_path.name}")
            runner.submit_chapter(index, str(text_path), str(output_path))
    finally:
        runner.close()

    print(f"Da tao lai {len(scripts)} voice va .segments.json trong: {voices_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
