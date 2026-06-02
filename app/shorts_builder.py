from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Callable

from .veo_prompts import STRICT_NO_TEXT_IMAGE_RULE, enforce_no_text_image_prompt, visual_safe_story_context
from .text_to_voice_queue import TextToVoiceRunner
from .video_editor import (
    VIDEO_SUFFIXES,
    caption_entries_for_segment,
    clean_caption_text,
    common_video_output_args,
    cover_video_filter,
    escape_ass_text,
    ffmpeg_base_args,
    format_ass_time,
    format_srt_time,
    natural_key,
    probe_duration,
    read_voice_timing_segments,
    run_process,
    timed_caption_entries,
    timed_caption_entries_from_segments,
    video_encoder_args,
    wrap_caption,
    write_concat_file,
)


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920
BLOCK_SECONDS = 8


def prepare_short_veo_prompt_file(project_dir: str | Path, limit: int = 8) -> tuple[list[str], Path, list[tuple[Path, int]]]:
    project = Path(project_dir)
    shorts_dir = project / "shorts"
    targets: list[tuple[Path, int]] = []
    prompts: list[str] = []
    max_count = max(1, min(int(limit or 8), 8))

    for short_dir in collect_short_dirs(project):
        prompt_paths = sorted(short_dir.glob("block_*.image_prompt.txt"), key=natural_key)
        if not prompt_paths:
            prompt_paths = sorted(short_dir.glob("block_*.veo_prompt.txt"), key=natural_key)
        for prompt_path in prompt_paths:
            match = re.search(r"block_(\d+)", prompt_path.stem)
            block_index = int(match.group(1)) if match else len(prompts) + 1
            prompt = prompt_path.read_text(encoding="utf-8", errors="ignore").strip()
            if not prompt:
                continue
            prompts.append(compose_short_image_prompt(short_dir, block_index, prompt))
            targets.append((short_dir, block_index))
            if len(prompts) >= max_count:
                break
        if len(prompts) >= max_count:
            break

    if not prompts:
        raise FileNotFoundError(f"Chua co prompt anh Short trong: {shorts_dir}. Hay chay workflow de tao shorts_package truoc.")

    out_dir = shorts_dir / "veo_videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "short_image_prompts_for_flow.txt"
    lines = [f"{index:02d}. {prompt}" for index, prompt in enumerate(prompts, start=1)]
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return prompts, out_path, targets


def compose_short_image_prompt(short_dir: Path, block_index: int, visual_prompt: str) -> str:
    voice = visual_safe_story_context(read_sidecar_text(short_dir / f"block_{block_index:02d}.voice.txt"), 360)
    negative = read_sidecar_text(short_dir / f"block_{block_index:02d}.negative_prompt.txt")
    metadata_block = read_metadata_block(short_dir, block_index)
    visual_context = visual_safe_story_context(str(metadata_block.get("visual_context") or "").strip(), 320) if metadata_block else ""
    visual_direction = visual_safe_story_context(visual_prompt, 420)

    parts = [
        "Create exactly one still image for a YouTube Short visual block.",
        "Format: 9:16 vertical, ultra-realistic American family revenge/betrayal drama, cinematic but natural.",
        "The image must capture one specific frozen moment, not a generic background and not a video scene.",
    ]
    if voice:
        parts.append(f"Story beat context only, never visible text: {voice}.")
        parts.append("The still image must match that voiceover moment through faces, posture, location, and props only.")
    if visual_context:
        parts.append(f"Additional non-text story context: {visual_context}")
    parts.append(f"Still image direction: {visual_direction}")
    parts.append("Use clear emotion, a concrete location, and one visible proof object or story action when relevant.")
    parts.append(STRICT_NO_TEXT_IMAGE_RULE)
    strict_negative = "words, letters, numbers, captions, subtitles, signs, logos, watermark, readable UI, app UI text, document text, phone-screen text, handwriting, typed text, typography, distorted hands"
    negative_text = f"{negative}, {strict_negative}" if negative else strict_negative
    parts.append(f"Negative prompt: {negative_text}")
    return enforce_no_text_image_prompt("\n".join(parts).strip())


def compose_short_veo_prompt(short_dir: Path, block_index: int, visual_prompt: str) -> str:
    return compose_short_image_prompt(short_dir, block_index, visual_prompt)


def read_sidecar_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def read_metadata_block(short_dir: Path, block_index: int) -> dict:
    metadata = read_short_metadata(short_dir)
    blocks = metadata.get("veo_blocks")
    if not isinstance(blocks, list):
        return {}
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            continue
        try:
            current = int(block.get("block") or index)
        except Exception:
            current = index
        if current == block_index:
            return block
    return {}


def collect_image_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES], key=natural_key)


def distribute_short_veo_videos(videos: list[Path], targets: list[tuple[Path, int]], log: Callable[[str], None] | None = None) -> None:
    distribute_short_media(videos, targets, "mp4", log=log)


def distribute_short_veo_images(images: list[Path], targets: list[tuple[Path, int]], log: Callable[[str], None] | None = None) -> None:
    distribute_short_media(images, targets, "jpg", log=log)


def distribute_short_media(
    files: list[Path],
    targets: list[tuple[Path, int]],
    fallback_suffix: str,
    log: Callable[[str], None] | None = None,
) -> None:
    if not files or not targets:
        return
    suffix = "." + fallback_suffix.lstrip(".")
    for source, (short_dir, block_index) in zip(sorted(files, key=natural_key), targets):
        media_dir = short_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / f"block_{block_index:02d}{source.suffix.lower() or suffix}"
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
        shutil.copy2(source, target)
        _log(log, f"Short media: {source.name} -> {target}")


def build_project_shorts(project_dir: str | Path, settings: dict, log: Callable[[str], None] | None = None) -> list[Path]:
    project = Path(project_dir)
    short_dirs = collect_short_dirs(project)
    if not short_dirs:
        raise FileNotFoundError(f"Chua co thu muc Short trong: {project / 'shorts'}")

    runner = TextToVoiceRunner(settings, log=lambda msg: _log(log, msg), stop_check=lambda: False)
    runner.start()
    outputs: list[Path] = []
    try:
        for short_dir in short_dirs:
            output = build_single_short(project, short_dir, settings, runner, log=log)
            outputs.append(output)
    finally:
        runner.close()
    return outputs


def build_single_short(
    project: Path,
    short_dir: Path,
    settings: dict,
    runner: TextToVoiceRunner,
    log: Callable[[str], None] | None = None,
) -> Path:
    metadata = read_short_metadata(short_dir)
    expected_duration = expected_short_duration(short_dir, metadata)

    voice_text = short_dir / "voice.txt"
    if not voice_text.exists() or not voice_text.read_text(encoding="utf-8", errors="ignore").strip():
        raise FileNotFoundError(f"Chua co voice.txt cho Short: {short_dir}")

    voice_path = Path(runner.submit_file(voice_text, short_dir.name, short_dir / "voice.wav"))
    duration = probe_duration(voice_path) or expected_duration
    block_count = max(1, int(math.ceil(duration / BLOCK_SECONDS)))

    cache_dir = short_dir / "_render_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    block_videos: list[Path] = []
    last_media: Path | None = None
    for index in range(1, block_count + 1):
        media = find_block_media(short_dir, index)
        if media is None and last_media is not None:
            media = last_media
            _log(log, f"Short {short_dir.name}: lap lai anh block truoc de phu het voice ({index:02d}).")
        if media is None:
            raise FileNotFoundError(
                f"Thieu media block {index:02d} cho {short_dir.name}. "
                "Bam 'Shorts: Tao anh' truoc, hoac bo file block_XX.jpg/png/webp vao thu muc media."
            )
        last_media = media
        block_out = cache_dir / f"block_{index:02d}.mp4"
        render_block_media(media, block_out, offset=(index - 1) * BLOCK_SECONDS)
        block_videos.append(block_out)

    visual_path = cache_dir / "visual.mp4"
    concat_path = cache_dir / "concat.txt"
    write_concat_file(block_videos, concat_path)
    run_process(
        [
            *ffmpeg_base_args(),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_path.name,
            "-an",
            "-t",
            f"{duration:.3f}",
            *common_video_output_args(visual_path.name, SHORT_WIDTH, SHORT_HEIGHT),
        ],
        cwd=cache_dir,
    )

    subtitle_path = short_dir / "short_subtitles.srt"
    write_short_subtitles(voice_path, voice_text.read_text(encoding="utf-8", errors="ignore"), subtitle_path, duration)

    output_path = short_dir / f"{short_dir.name}.mp4"
    render_short_final(visual_path, voice_path, subtitle_path.with_suffix(".ass"), output_path, duration)
    _log(log, f"Short da render: {output_path}")
    return output_path


def collect_short_dirs(project: Path) -> list[Path]:
    shorts_dir = project / "shorts"
    if not shorts_dir.exists():
        return []
    dirs = [p for p in shorts_dir.iterdir() if p.is_dir() and p.name.lower().startswith("short_")]
    return sorted(dirs, key=natural_key)


def read_short_metadata(short_dir: Path) -> dict:
    path = short_dir / "metadata.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def expected_short_duration(short_dir: Path, metadata: dict) -> float:
    try:
        value = float(metadata.get("duration_seconds") or 0)
        if value > 0:
            return value
    except Exception:
        pass
    if "01" in short_dir.name:
        return 24.0
    return 40.0


def find_block_media(short_dir: Path, block_index: int) -> Path | None:
    stems = [f"block_{block_index:02d}", f"{block_index:02d}"]
    search_dirs = [short_dir / "media", short_dir]
    for directory in search_dirs:
        if not directory.exists():
            continue
        for stem in stems:
            for suffix in IMAGE_SUFFIXES:
                candidate = directory / f"{stem}{suffix}"
                if candidate.exists():
                    return candidate
    return None


def render_block_media(media: Path, output_path: Path, offset: float = 0.0) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = media.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        frames = max(1, int(round(BLOCK_SECONDS * 24)))
        effect = int(offset // BLOCK_SECONDS) % 4
        if effect == 0:
            motion = (
                f"zoompan=z='min(zoom+0.0013,1.12)':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={SHORT_WIDTH}x{SHORT_HEIGHT}:fps=24"
            )
        elif effect == 1:
            motion = (
                f"zoompan=z='1.10-min(on*0.0010,0.08)':d=1:"
                f"x='(iw-iw/zoom)*(on/{frames})':y='ih/2-(ih/zoom/2)':s={SHORT_WIDTH}x{SHORT_HEIGHT}:fps=24"
            )
        elif effect == 2:
            motion = (
                f"zoompan=z='min(zoom+0.0010,1.10)':d=1:"
                f"x='(iw-iw/zoom)*(1-on/{frames})':y='(ih-ih/zoom)*(on/{frames})':s={SHORT_WIDTH}x{SHORT_HEIGHT}:fps=24"
            )
        else:
            motion = (
                f"zoompan=z='1.08':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-on/{frames})':s={SHORT_WIDTH}x{SHORT_HEIGHT}:fps=24"
            )
        vf = (
            f"scale={SHORT_WIDTH}:{SHORT_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={SHORT_WIDTH}:{SHORT_HEIGHT},setsar=1,{motion},trim=duration={BLOCK_SECONDS:.3f},format=yuv420p"
        )
        run_process(
            [
                *ffmpeg_base_args(),
                "-loop",
                "1",
                "-t",
                f"{BLOCK_SECONDS:.3f}",
                "-i",
                str(media),
                "-vf",
                vf,
                "-frames:v",
                str(frames),
                "-an",
                *common_video_output_args(str(output_path), SHORT_WIDTH, SHORT_HEIGHT),
            ]
        )
        return

    run_process(
        [
            *ffmpeg_base_args(),
            "-stream_loop",
            "-1",
            "-ss",
            f"{max(0.0, float(offset or 0.0)):.3f}",
            "-i",
            str(media),
            "-t",
            f"{BLOCK_SECONDS:.3f}",
            "-vf",
            cover_video_filter(SHORT_WIDTH, SHORT_HEIGHT, remove_logo=True),
            "-an",
            *common_video_output_args(str(output_path), SHORT_WIDTH, SHORT_HEIGHT),
        ]
    )


def write_short_subtitles(voice_path: Path, voice_text: str, subtitle_path: Path, duration: float) -> None:
    segments = read_voice_timing_segments(voice_path)
    if segments:
        entries = timed_caption_entries_from_segments(segments, 0.0)
    else:
        entries = timed_caption_entries(voice_text, 0.0, duration, base_speed=1.0, delivery="dramatic")
    entries = [(max(0.0, s), min(duration, e), t) for s, e, t in entries if t and e > s and s < duration]
    if not entries:
        entries = caption_entries_for_segment(clean_caption_text(voice_text), 0.0, duration)

    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        if not text.strip():
            continue
        lines.append(str(index))
        lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        lines.append(wrap_caption(text, width=28))
        lines.append("")
    subtitle_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    write_short_ass_subtitles(entries, subtitle_path.with_suffix(".ass"))


def write_short_ass_subtitles(entries: list[tuple[float, float, str]], output_path: Path) -> None:
    font_size = 64
    margin_v = 250
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {SHORT_WIDTH}",
        f"PlayResY: {SHORT_HEIGHT}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: ShortCaption,Arial,{font_size},&H00FFFFFF,&H000000FF,&H9A000000,&H66000000,"
        f"1,0,0,0,100,100,0,0,1,4,2,2,70,70,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in entries:
        caption = escape_ass_text(wrap_caption(text, width=26))
        if caption.strip():
            lines.append(f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},ShortCaption,,0,0,0,,{caption}")
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def render_short_final(visual_path: Path, voice_path: Path, ass_path: Path, output_path: Path, duration: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_filter = f"subtitles={ass_path.name}" if ass_path.exists() else "null"
    run_process(
        [
            *ffmpeg_base_args(),
            "-i",
            str(visual_path),
            "-i",
            str(voice_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            *video_encoder_args(SHORT_WIDTH, SHORT_HEIGHT),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        cwd=ass_path.parent,
    )


def _log(callback: Callable[[str], None] | None, message: str) -> None:
    if callable(callback):
        callback(str(message or ""))
