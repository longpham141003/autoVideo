from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".webm"}
MERGED_VIDEO_NAME = "merged_veo_video.mp4"
SUBTITLE_ADVANCE_SECONDS = 0.0
SUBTITLE_MAX_CHARS = 68
SUBTITLE_WRAP_WIDTH = 48
_ENCODER_CACHE: str | None = None
_DURATION_CACHE: dict[tuple[str, int, int], float] = {}
_VIDEO_INFO_CACHE: dict[tuple[str, int, int], tuple[int, int]] = {}
INTRO_VIDEO_HISTORY_LIMIT = 10
INTRO_VIDEO_HISTORY_PATH = Path(__file__).resolve().parents[1] / "intro_video_history.json"
BACKGROUND_VIDEO_HISTORY_LIMIT = 120
BACKGROUND_VIDEO_HISTORY_PATH = Path(__file__).resolve().parents[1] / "background_video_history.json"
BACKGROUND_VIDEO_MODES = [
    ("cooking", "Nau an", ("cooking",)),
    ("house", "Sinh hoat / lam viec nha", ("house",)),
    ("scenery", "Phong canh / duong / mua", ("nature", "rain", "driving", "street")),
    ("office", "Van phong / giay to", ("office",)),
    ("wedding", "Dam cuoi / tiec", ("wedding",)),
]


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


DELIVERY_STYLES = {
    "plain": {
        "sentencePause": 0.08,
        "paragraphPause": 0.25,
        "speedBias": 1.0,
        "questionSpeed": 1.0,
        "exclaimSpeed": 1.0,
        "shortSpeed": 1.0,
    },
    "natural": {
        "sentencePause": 0.12,
        "paragraphPause": 0.28,
        "speedBias": 1.0,
        "questionSpeed": 0.98,
        "exclaimSpeed": 0.99,
        "shortSpeed": 0.98,
    },
    "expressive": {
        "sentencePause": 0.16,
        "paragraphPause": 0.36,
        "speedBias": 1.0,
        "questionSpeed": 0.96,
        "exclaimSpeed": 0.98,
        "shortSpeed": 0.96,
    },
    "dramatic": {
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
        "sentencePause": 0.18,
        "paragraphPause": 0.42,
        "speedBias": 0.99,
        "questionSpeed": 0.95,
        "exclaimSpeed": 0.98,
        "shortSpeed": 0.95,
    },
    "calm": {
        "sentencePause": 0.38,
        "paragraphPause": 0.9,
        "speedBias": 0.88,
        "questionSpeed": 0.92,
        "exclaimSpeed": 0.93,
        "shortSpeed": 0.91,
    },
}


@dataclass
class RenderResult:
    output_path: Path
    audio_path: Path
    subtitle_path: Path
    video_count: int
    voice_count: int
    duration: float


def render_project_video(
    project_dir: str | Path,
    settings: dict | None = None,
    log: Callable[[str], None] | None = None,
    background_files: list[Path] | None = None,
) -> RenderResult:
    settings = settings or {}
    project = Path(project_dir)
    if not project.exists():
        raise FileNotFoundError(f"Khong thay project: {project}")

    background_dir = project / "veo_videos"
    output_dir = project / "final"
    background_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    width = _setting_int(settings, "final_video_width", 1920, 360, 3840)
    height = _setting_int(settings, "final_video_height", 1080, 360, 3840)
    layout = str(settings.get("final_video_layout") or "character_drama").strip().lower()
    allow_loop = _setting_bool(settings, "final_video_allow_loop", False)

    voices = collect_voice_files(project)
    raw_visuals = [Path(p) for p in background_files] if background_files else collect_project_media_files(project, background_dir)
    story_overlay_images: list[Path] = []
    using_background_broll = False
    videos = raw_visuals
    selected_broll_mode = normalize_background_video_mode(settings.get("background_video_mode"))
    selected_broll_files: list[Path] = []
    if layout == "character_drama":
        broll_videos = collect_background_broll_files(project, settings)
        raw_images = [path for path in raw_visuals if path.suffix.lower() in IMAGE_SUFFIXES]
        raw_videos = [path for path in raw_visuals if path.suffix.lower() in VIDEO_SUFFIXES]
        if broll_videos and raw_images:
            videos, selected_intro_video = prioritize_fresh_background_sequence(selected_broll_mode, broll_videos, log=log)
            selected_broll_files = list(videos)
            story_overlay_images = raw_images
            using_background_broll = True
            allow_loop = True
        elif broll_videos:
            videos, selected_intro_video = prioritize_fresh_background_sequence(selected_broll_mode, broll_videos, log=log)
            selected_broll_files = list(videos)
            using_background_broll = True
            allow_loop = True
        elif raw_videos and raw_images:
            videos = raw_videos
            story_overlay_images = raw_images
            selected_intro_video = None
        else:
            selected_intro_video = None
    else:
        selected_intro_video = None

    image_only_visuals = bool(videos) and all(path.suffix.lower() in IMAGE_SUFFIXES for path in videos)
    scripts = collect_script_files(project)

    if not voices:
        raise RuntimeError(f"Chua co voice trong: {project / 'voices'}")
    if not videos:
        raise RuntimeError(f"Chua co visual nen. Hay bo file anh vao: {background_dir / 'image'}")

    character_path = resolve_character_path(project, settings) if layout == "character_drama" else None
    remove_logo = (
        (not using_background_broll)
        and (not image_only_visuals)
        and not (len(videos) == 1 and Path(videos[0]).name == MERGED_VIDEO_NAME)
    )

    visual_kind = "anh nen" if image_only_visuals else "video nen"
    overlay_note = f", {len(story_overlay_images)} anh AI overlay" if story_overlay_images else ""
    _log(log, f"Auto edit: {len(voices)} voice, {len(videos)} {visual_kind}{overlay_note}, khung {width}x{height}, layout={layout}.")
    audio_path = output_dir / "final_voice.wav"
    subtitle_path = output_dir / "final_subtitles.srt"
    output_path = output_dir / "final_video.mp4"

    render_start = time.time()
    audio_start = time.time()
    combine_audio_files(voices, audio_path, log=log)
    _log(log, f"Thoi gian ghep voice: {format_elapsed(time.time() - audio_start)}")
    duration = probe_duration(audio_path)
    if duration <= 0:
        raise RuntimeError("Khong doc duoc do dai audio sau khi ghep voice.")
    target_image_count = max(1, int(math.ceil(duration / 60.0)))
    _log(log, f"Tong voice: {format_elapsed(duration)}. Muc tieu visual: moi phut 1 anh, can khoang {target_image_count} anh.")
    image_durations = None
    if image_only_visuals:
        image_durations = build_image_timeline_for_voice(project, voices, videos, output_dir, duration, log=log)
    videos = prepare_visual_media_for_render(
        videos,
        output_dir,
        width,
        height,
        settings,
        log=log,
        image_durations=image_durations,
    )

    subtitle_start = time.time()
    timing_count = count_voice_timing_files(voices)
    if timing_count == len(voices):
        _log(log, f"Sub sync: dung timing that tu {timing_count}/{len(voices)} voice.")
    else:
        _log(
            log,
            f"Sub sync: chi co {timing_count}/{len(voices)} voice co timing that. "
            "Hay tao lai voice de sub khop chinh xac hon.",
        )
    write_subtitles_for_project(
        scripts,
        voices,
        subtitle_path,
        total_duration=duration,
        width=width,
        height=height,
        settings=settings,
    )
    _log(log, f"Thoi gian tao sub: {format_elapsed(time.time() - subtitle_start)}")
    video_start = time.time()
    render_background_with_audio(
        videos=videos,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        output_path=output_path,
        duration=duration,
        width=width,
        height=height,
        layout=layout,
        character_path=character_path,
        remove_logo=remove_logo,
        allow_loop=allow_loop,
        story_overlay_images=story_overlay_images,
        log=log,
    )
    if selected_broll_files:
        remember_background_mode_usage(selected_broll_mode, selected_broll_files, log=log)
    elif selected_intro_video is not None:
        remember_intro_video(selected_intro_video, log=log)
    _log(log, f"Thoi gian render video FFmpeg: {format_elapsed(time.time() - video_start)}")
    _log(log, f"Da xuat video: {output_path}")
    _log(log, f"Thoi gian render final: {format_elapsed(time.time() - render_start)}")
    return RenderResult(
        output_path=output_path,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        video_count=len(videos),
        voice_count=len(voices),
        duration=duration,
    )


def merge_project_videos(
    project_dir: str | Path,
    settings: dict | None = None,
    log: Callable[[str], None] | None = None,
    background_files: list[Path] | None = None,
) -> Path:
    settings = settings or {}
    project = Path(project_dir)
    background_dir = project / "veo_videos"
    output_dir = project / "final"
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = [Path(p) for p in background_files] if background_files else collect_video_files(background_dir)
    if not videos:
        raise RuntimeError(f"Chua co video VEO trong: {background_dir}")

    width = _setting_int(settings, "final_video_width", 1920, 360, 3840)
    height = _setting_int(settings, "final_video_height", 1080, 360, 3840)
    output_path = output_dir / MERGED_VIDEO_NAME
    concat_path = output_dir / "video_concat_merge.txt"
    write_concat_file(videos, concat_path)

    if is_output_fresh(output_path, videos) and probe_video_size(output_path) == (width, height):
        _log(log, f"Da co video VEO da ghep, dung lai: {output_path}")
        return output_path

    _log(log, f"Ghep {len(videos)} video VEO va xoa logo VEO... encoder={selected_video_encoder()}")
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
            "-vf",
            cover_video_filter(width, height),
            *common_video_output_args(output_path.name, width, height),
        ],
        cwd=output_dir,
    )
    _log(log, f"Da ghep video VEO: {output_path}")
    return output_path


def collect_voice_files(project: Path) -> list[Path]:
    voice_dir = project / "voices"
    files = [p for p in voice_dir.glob("*") if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES]
    chapter_files = [p for p in files if re.fullmatch(r"chapter_\d+", p.stem, flags=re.IGNORECASE)]
    return sorted(chapter_files or files, key=natural_key)


def collect_video_files(background_dir: Path) -> list[Path]:
    if not background_dir.exists():
        return []
    return sorted([p for p in background_dir.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES], key=natural_key)


def collect_image_files(background_dir: Path) -> list[Path]:
    if not background_dir.exists():
        return []
    return sorted([p for p in background_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES], key=natural_key)


def collect_media_files(background_dir: Path) -> list[Path]:
    return sorted(collect_video_files(background_dir) + collect_image_files(background_dir), key=natural_key)


def collect_project_media_files(project: Path, background_dir: Path) -> list[Path]:
    return sorted(collect_video_files(background_dir) + collect_story_image_files(project, background_dir), key=natural_key)


def collect_story_image_files(project: Path, background_dir: Path) -> list[Path]:
    images = collect_image_files(background_dir)
    expected_count = expected_story_image_count(project)
    if expected_count <= 0 or len(images) <= expected_count:
        return images

    by_prompt: dict[int, list[Path]] = {}
    for image in images:
        match = re.match(r"^(\d{1,4})[_\-.]", image.name)
        if not match:
            continue
        index = int(match.group(1))
        if 1 <= index <= expected_count:
            by_prompt.setdefault(index, []).append(image)
    if len(by_prompt) < max(1, expected_count // 2):
        return images

    selected: list[Path] = []
    for index in range(1, expected_count + 1):
        candidates = by_prompt.get(index) or []
        if candidates:
            selected.append(max(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0))
    if len(selected) >= max(1, expected_count // 2):
        return sorted(selected, key=natural_key)
    return images


def expected_story_image_count(project: Path) -> int:
    timeline_path = project / "veo_videos" / "image_prompt_timeline.json"
    try:
        data = json.loads(timeline_path.read_text(encoding="utf-8"))
        blocks = data.get("blocks") if isinstance(data, dict) else None
        if isinstance(blocks, list) and blocks:
            return len(blocks)
    except Exception:
        pass

    for path in (
        project / "veo_videos" / "image_prompts_for_flow.txt",
        project / "artifacts" / "image_prompts_for_flow.txt",
        project / "artifacts" / "veo3_prompts.txt",
    ):
        count = count_prompt_lines(path)
        if count > 0:
            return count
    return 0


def count_prompt_lines(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    return len(re.findall(r"(?im)^\s*(?:prompt\s*)?\d{1,4}\s*[\).:\-]\s*\S", text))


def collect_background_broll_files(project: Path, settings: dict) -> list[Path]:
    mode = normalize_background_video_mode(settings.get("background_video_mode"))
    allowed_dirs = set(background_video_mode_dirs(mode))
    roots: list[Path] = []
    raw = str(settings.get("background_videos_dir") or "").strip()
    if raw:
        path = Path(raw)
        roots.append(path if path.is_absolute() else project / path)
    roots.extend(
        [
            project / "BackgroundVideos",
            project / "background_videos",
            Path(__file__).resolve().parents[1] / "BackgroundVideos",
        ]
    )
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), key=natural_key):
            if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if allowed_dirs and background_video_category(root, path) not in allowed_dirs:
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return sort_background_videos_by_duration(files)


def sort_background_videos_by_duration(files: list[Path]) -> list[Path]:
    return sorted(files, key=lambda path: (-probe_duration(path), natural_key(path)))


def background_video_mode_options() -> list[tuple[str, str, str | None]]:
    history = load_background_video_history()
    rows: list[tuple[str, str, str | None]] = []
    for index, (mode, label, _dirs) in enumerate(BACKGROUND_VIDEO_MODES, start=1):
        last_used = background_mode_last_used(mode, history)
        display_time = format_background_last_used(last_used)
        rows.append((mode, f"{index}. {label} (gan nhat: {display_time})", last_used))
    return rows


def normalize_background_video_mode(value) -> str:
    raw = str(value or "").strip().lower()
    valid = {mode for mode, _label, _dirs in BACKGROUND_VIDEO_MODES}
    return raw if raw in valid else BACKGROUND_VIDEO_MODES[0][0]


def background_video_mode_dirs(mode: str) -> tuple[str, ...]:
    normalized = normalize_background_video_mode(mode)
    for item_mode, _label, dirs in BACKGROUND_VIDEO_MODES:
        if item_mode == normalized:
            return dirs
    return BACKGROUND_VIDEO_MODES[0][2]


def background_video_category(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root)
        if rel.parts:
            return str(rel.parts[0]).lower()
    except Exception:
        pass
    return path.parent.name.lower()


def prioritize_fresh_background_sequence(
    mode: str,
    videos: list[Path],
    log: Callable[[str], None] | None = None,
) -> tuple[list[Path], Path | None]:
    if not videos:
        return videos, None
    normalized = normalize_background_video_mode(mode)
    history = load_background_video_history()
    recent = background_mode_recent_keys(normalized, history)
    fresh = [path for path in videos if intro_video_history_key(path) not in recent]
    stale = [path for path in videos if intro_video_history_key(path) in recent]
    if fresh:
        ordered = fresh + stale
        _log(log, f"Nen video mode={normalized}: uu tien {len(fresh)} clip chua nam trong lich su gan day.")
    else:
        offset = background_mode_next_offset(normalized, len(videos), history)
        ordered = videos[offset:] + videos[:offset]
        _log(log, f"Nen video mode={normalized}: kho da dung het gan day, xoay vong tu vi tri {offset + 1}.")
    return ordered, ordered[0] if ordered else None


def load_background_video_history() -> dict:
    try:
        data = json.loads(BACKGROUND_VIDEO_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "modes": {}}
    if not isinstance(data, dict):
        return {"version": 1, "modes": {}}
    modes = data.get("modes")
    if not isinstance(modes, dict):
        data["modes"] = {}
    return data


def background_mode_recent_keys(mode: str, history: dict | None = None) -> set[str]:
    data = history or load_background_video_history()
    modes = data.get("modes") if isinstance(data, dict) else {}
    info = modes.get(normalize_background_video_mode(mode)) if isinstance(modes, dict) else {}
    items = info.get("items") if isinstance(info, dict) else []
    if not isinstance(items, list):
        return set()
    return {str(item).strip().lower() for item in items[-BACKGROUND_VIDEO_HISTORY_LIMIT:] if str(item).strip()}


def background_mode_last_used(mode: str, history: dict | None = None) -> str | None:
    data = history or load_background_video_history()
    modes = data.get("modes") if isinstance(data, dict) else {}
    info = modes.get(normalize_background_video_mode(mode)) if isinstance(modes, dict) else {}
    value = info.get("last_used") if isinstance(info, dict) else None
    return str(value).strip() if value else None


def background_mode_next_offset(mode: str, video_count: int, history: dict | None = None) -> int:
    if video_count <= 0:
        return 0
    data = history or load_background_video_history()
    modes = data.get("modes") if isinstance(data, dict) else {}
    info = modes.get(normalize_background_video_mode(mode)) if isinstance(modes, dict) else {}
    try:
        return int(info.get("next_offset") or 0) % video_count if isinstance(info, dict) else 0
    except Exception:
        return 0


def format_background_last_used(value: str | None) -> str:
    if not value:
        return "chua dung"
    return value.replace("T", " ")[:16]


def remember_background_mode_usage(mode: str, videos: list[Path], log: Callable[[str], None] | None = None) -> None:
    normalized = normalize_background_video_mode(mode)
    data = load_background_video_history()
    modes = data.setdefault("modes", {})
    if not isinstance(modes, dict):
        modes = {}
        data["modes"] = modes
    info = modes.get(normalized)
    if not isinstance(info, dict):
        info = {}
    history = info.get("items") if isinstance(info.get("items"), list) else []
    history = [str(item).strip().lower() for item in history if str(item).strip()]
    for video in videos:
        key = intro_video_history_key(video)
        history = [item for item in history if item != key]
        history.append(key)
    info["items"] = history[-BACKGROUND_VIDEO_HISTORY_LIMIT:]
    info["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if videos:
        try:
            previous_offset = int(info.get("next_offset") or 0)
        except Exception:
            previous_offset = 0
        step = max(1, len(videos) // 3)
        info["next_offset"] = (previous_offset + step) % max(1, len(videos))
    modes[normalized] = info
    data["version"] = 1
    BACKGROUND_VIDEO_HISTORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if videos:
        _log(log, f"Da cap nhat lich su nen mode={normalized}: {len(videos)} clip.")


def prioritize_fresh_intro_video(
    videos: list[Path],
    log: Callable[[str], None] | None = None,
) -> tuple[list[Path], Path | None]:
    if not videos:
        return videos, None

    recent = load_intro_video_history()
    selected = next((path for path in videos if intro_video_history_key(path) not in recent), None)
    if selected is None:
        selected = videos[0]
        _log(log, "Video mo dau: 10 clip gan nhat da phu het kho hien co, dung lai clip dau tien theo thu tu.")
        return videos, selected

    if selected != videos[0]:
        reordered = [selected, *[path for path in videos if path != selected]]
        _log(log, f"Video mo dau: uu tien clip moi chua nam trong lich su 10 lan gan nhat: {selected.name}")
        return reordered, selected

    _log(log, f"Video mo dau: clip dau tien hien tai chua nam trong lich su 10 lan gan nhat: {selected.name}")
    return videos, selected


def load_intro_video_history() -> set[str]:
    try:
        data = json.loads(INTRO_VIDEO_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return set()
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return set()
    keys: set[str] = set()
    for item in raw_items[-INTRO_VIDEO_HISTORY_LIMIT:]:
        if isinstance(item, str) and item.strip():
            keys.add(item.strip().lower())
    return keys


def remember_intro_video(video: Path, log: Callable[[str], None] | None = None) -> None:
    key = intro_video_history_key(video)
    history: list[str] = []
    try:
        data = json.loads(INTRO_VIDEO_HISTORY_PATH.read_text(encoding="utf-8"))
        raw_items = data.get("items") if isinstance(data, dict) else None
        if isinstance(raw_items, list):
            history = [str(item).strip().lower() for item in raw_items if str(item).strip()]
    except Exception:
        history = []

    history = [item for item in history if item != key]
    history.append(key)
    history = history[-INTRO_VIDEO_HISTORY_LIMIT:]
    INTRO_VIDEO_HISTORY_PATH.write_text(
        json.dumps({"version": 1, "items": history}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log(log, f"Da cap nhat lich su video mo dau: {video.name}")


def intro_video_history_key(video: Path) -> str:
    try:
        return str(video.resolve()).lower()
    except Exception:
        return str(video).lower()


def collect_script_files(project: Path) -> list[Path]:
    script_dir = project / "scripts"
    return sorted(script_dir.glob("chapter_*.txt"), key=natural_key)


def combine_audio_files(voices: list[Path], output_path: Path, log: Callable[[str], None] | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(voices) == 1:
        _log(log, "Ghep voice: 1 file.")
        if is_output_fresh(output_path, voices) and probe_duration(output_path) > 0:
            _log(log, "Dung lai file voice da ghep.")
            return
        run_process(
            [
                *ffmpeg_base_args(),
                "-i",
                str(voices[0]),
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        return

    _log(log, f"Ghep voice: {len(voices)} file.")
    if is_output_fresh(output_path, voices) and probe_duration(output_path) > 0:
        _log(log, "Dung lai file voice da ghep.")
        return
    concat_path = output_path.parent / "voice_concat.txt"
    write_concat_file(voices, concat_path)
    run_process(
        [
            *ffmpeg_base_args(),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )


def prepare_visual_media_for_render(
    media: list[Path],
    output_dir: Path,
    width: int,
    height: int,
    settings: dict,
    log: Callable[[str], None] | None = None,
    image_durations: list[float] | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_duration = _setting_float(settings, "final_image_duration", 18.0, 4.0, 60.0)
    cache_dir = output_dir / "_visual_image_clips"
    video_cache_dir = output_dir / "_visual_video_clips"
    prepared: list[Path] = []
    image_count = 0
    video_count = 0
    for index, path in enumerate(media, start=1):
        if path.suffix.lower() in IMAGE_SUFFIXES:
            image_count += 1
            duration = image_duration
            if image_durations and image_count <= len(image_durations):
                duration = max(0.05, float(image_durations[image_count - 1] or image_duration))
            prepared.append(render_image_as_static_clip(path, cache_dir, index, width, height, duration))
        elif path.suffix.lower() in VIDEO_SUFFIXES:
            video_count += 1
            prepared.append(render_video_as_normalized_clip(path, video_cache_dir, index, width, height))
        else:
            prepared.append(path)
    if image_count:
        if image_durations:
            _log(
                log,
                f"Visual media: da chuyen {image_count} anh thanh clip tinh theo timeline voice, "
                f"tong {format_elapsed(sum(image_durations[:image_count]))}.",
            )
        else:
            _log(log, f"Visual media: da chuyen {image_count} anh thanh clip tinh {image_duration:g}s.")
    if video_count:
        _log(log, f"Visual media: da chuan hoa {video_count} video nen ve cung khung/FPS de tranh dung hinh.")
    return prepared


def build_image_timeline_for_voice(
    project: Path,
    voices: list[Path],
    images: list[Path],
    output_dir: Path,
    total_duration: float,
    log: Callable[[str], None] | None = None,
) -> list[float]:
    image_count = len([path for path in images if path.suffix.lower() in IMAGE_SUFFIXES])
    if image_count <= 0:
        return []

    blocks = load_prompt_timeline_blocks(project, voices, image_count, total_duration)
    source = "prompt voice blocks"
    if not blocks:
        blocks = voice_segment_time_windows(voices, image_count, total_duration)
        source = "voice timing"
    if not blocks:
        blocks = fixed_time_windows(image_count, total_duration)
        source = "chia deu"

    durations = frame_safe_durations([float(block["end"]) - float(block["start"]) for block in blocks], total_duration)
    write_image_timeline_file(output_dir / "image_timeline.json", images, blocks, durations, total_duration, source)
    _log(log, f"Image timeline: {image_count} anh bam theo {source}, audio {format_elapsed(total_duration)}.")
    return durations


def load_prompt_timeline_blocks(project: Path, voices: list[Path], image_count: int, total_duration: float) -> list[dict]:
    timeline_path = project / "veo_videos" / "image_prompt_timeline.json"
    if not timeline_path.exists():
        return []
    try:
        data = json.loads(timeline_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(raw_blocks, list):
        return []
    source = str(data.get("source") or "").lower() if isinstance(data, dict) else ""
    already_global_timing = "minute" in source

    chapter_offsets = chapter_voice_offsets(voices)
    timed: list[dict] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        try:
            chapter = int(raw.get("chapter") or 1)
            start = float(raw.get("start") or 0.0)
            end = float(raw.get("end") or 0.0)
        except Exception:
            continue
        if end <= start:
            continue
        offset = 0.0 if already_global_timing else (chapter_offsets[chapter - 1] if 0 <= chapter - 1 < len(chapter_offsets) else 0.0)
        timed.append(
            {
                "start": max(0.0, offset + start),
                "end": min(max(0.0, total_duration), offset + end),
                "text": trim_timeline_excerpt(str(raw.get("voice_excerpt") or "")),
            }
        )

    if len(timed) < image_count:
        return []
    if len(timed) > image_count:
        timed = merge_timed_blocks_to_count(timed, image_count)
    return contiguous_timeline_blocks(timed, image_count, total_duration)


def chapter_voice_offsets(voices: list[Path]) -> list[float]:
    offsets: list[float] = []
    cursor = 0.0
    for voice in voices:
        offsets.append(cursor)
        cursor += max(0.0, probe_duration(voice))
    return offsets


def merge_timed_blocks_to_count(blocks: list[dict], target_count: int) -> list[dict]:
    if len(blocks) <= target_count:
        return blocks
    merged: list[dict] = []
    bucket = len(blocks) / float(max(1, target_count))
    cursor = 0.0
    for _index in range(target_count):
        start_index = int(round(cursor))
        cursor += bucket
        end_index = int(round(cursor))
        group = blocks[start_index:max(end_index, start_index + 1)]
        if not group:
            continue
        merged.append(
            {
                "start": float(group[0].get("start") or 0.0),
                "end": float(group[-1].get("end") or group[0].get("end") or 0.0),
                "text": trim_timeline_excerpt(" ".join(str(item.get("text") or "") for item in group)),
            }
        )
    return merged


def voice_segment_time_windows(voices: list[Path], image_count: int, total_duration: float) -> list[dict]:
    segments: list[dict] = []
    cursor = 0.0
    for voice in voices:
        for segment in read_voice_timing_segments(voice):
            text = clean_caption_text(str(segment.get("text") or ""))
            if not text:
                continue
            try:
                start = cursor + max(0.0, float(segment.get("start") or 0.0))
                end = cursor + max(0.0, float(segment.get("end") or 0.0))
            except Exception:
                continue
            if end > start:
                segments.append({"start": start, "end": end, "text": text})
        cursor += max(0.0, probe_duration(voice))
    if not segments:
        return []

    blocks: list[dict] = []
    for index in range(image_count):
        start = total_duration * index / float(image_count)
        end = total_duration * (index + 1) / float(image_count)
        text_parts = [
            str(segment.get("text") or "")
            for segment in segments
            if float(segment.get("end") or 0.0) > start and float(segment.get("start") or 0.0) < end
        ]
        if not text_parts:
            center = start + (end - start) / 2.0
            nearest = min(
                segments,
                key=lambda item: abs(((float(item.get("start") or 0.0) + float(item.get("end") or 0.0)) / 2.0) - center),
            )
            text_parts = [str(nearest.get("text") or "")]
        blocks.append({"start": start, "end": end, "text": trim_timeline_excerpt(" ".join(text_parts))})
    return blocks


def fixed_time_windows(image_count: int, total_duration: float) -> list[dict]:
    return [
        {
            "start": total_duration * index / float(image_count),
            "end": total_duration * (index + 1) / float(image_count),
            "text": "",
        }
        for index in range(image_count)
    ]


def contiguous_timeline_blocks(blocks: list[dict], image_count: int, total_duration: float) -> list[dict]:
    if len(blocks) != image_count or image_count <= 0:
        return []
    total = max(0.0, float(total_duration or 0.0))
    if total <= 0:
        return []

    average = total / float(image_count)
    min_step = min(1.0, max(0.05, average * 0.25))
    boundaries = [0.0]
    for index in range(1, image_count):
        fallback = total * index / float(image_count)
        try:
            candidate = float(blocks[index].get("start") or fallback)
        except Exception:
            candidate = fallback
        lower = boundaries[-1] + min_step
        upper = total - min_step * (image_count - index)
        if lower > upper:
            lower = boundaries[-1]
            upper = total
        boundaries.append(clamp_float(candidate, lower, upper))
    boundaries.append(total)

    normalized: list[dict] = []
    for index, block in enumerate(blocks):
        normalized.append(
            {
                "start": boundaries[index],
                "end": boundaries[index + 1],
                "voice_start": float(block.get("start") or boundaries[index]),
                "voice_end": float(block.get("end") or boundaries[index + 1]),
                "text": trim_timeline_excerpt(str(block.get("text") or "")),
            }
        )
    return normalized


def frame_safe_durations(durations: list[float], total_duration: float, fps: int = 24) -> list[float]:
    if not durations:
        return []
    average = max(0.0, float(total_duration or 0.0)) / float(max(1, len(durations)))
    min_frames = 12 if average >= 1.0 else 1
    frame_counts = [max(min_frames, int(math.ceil(max(0.01, float(duration or 0.0)) * fps))) for duration in durations]
    target_frames = int(math.ceil((max(0.0, float(total_duration or 0.0)) + 0.30) * fps))
    missing = target_frames - sum(frame_counts)
    if missing > 0:
        frame_counts[-1] += missing
    return [frames / float(fps) for frames in frame_counts]


def write_image_timeline_file(
    output_path: Path,
    images: list[Path],
    blocks: list[dict],
    durations: list[float],
    total_duration: float,
    source: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    cursor = 0.0
    image_paths = [path for path in images if path.suffix.lower() in IMAGE_SUFFIXES]
    for index, (image, duration) in enumerate(zip(image_paths, durations), start=1):
        block = blocks[index - 1] if index - 1 < len(blocks) else {}
        end = cursor + float(duration)
        records.append(
            {
                "index": index,
                "image": image.name,
                "start": round(cursor, 3),
                "end": round(end, 3),
                "duration": round(float(duration), 3),
                "voice_start": round(float(block.get("voice_start", block.get("start", cursor)) or 0.0), 3),
                "voice_end": round(float(block.get("voice_end", block.get("end", end)) or 0.0), 3),
                "voice_excerpt": trim_timeline_excerpt(str(block.get("text") or ""), 220),
            }
        )
        cursor = end
    output_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source": source,
                "audio_duration": round(float(total_duration or 0.0), 3),
                "visual_duration": round(cursor, 3),
                "items": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def trim_timeline_excerpt(text: str, limit: int = 260) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[: max(40, int(limit))]
    last_space = cut.rfind(" ")
    if last_space > 60:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:") + "..."


def render_image_as_static_clip(image_path: Path, cache_dir: Path, index: int, width: int, height: int, duration: float) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", image_path.stem).strip("_") or f"image_{index:04d}"
    output_path = cache_dir / f"{index:04d}_{safe_stem}_staticv1_{int(width)}x{int(height)}_{int(duration * 1000)}ms.mp4"
    if is_output_fresh(output_path, [image_path]) and probe_duration(output_path) > 0:
        return output_path
    frames = max(1, int(round(float(duration) * 24)))
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,trim=duration={duration:.3f},"
        "fps=24,settb=AVTB,setpts=N/(24*TB),format=yuv420p"
    )
    run_process(
        [
            *ffmpeg_base_args(),
            "-loop",
            "1",
            "-i",
            str(image_path.resolve()),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-frames:v",
            str(frames),
            "-r",
            "24",
            *video_encoder_args(width, height),
            output_path.name,
        ],
        cwd=cache_dir,
    )
    return output_path


def render_image_as_motion_clip(image_path: Path, cache_dir: Path, index: int, width: int, height: int, duration: float) -> Path:
    return render_image_as_static_clip(image_path, cache_dir, index, width, height, duration)


def render_video_as_normalized_clip(
    video_path: Path,
    cache_dir: Path,
    index: int,
    width: int,
    height: int,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", video_path.stem).strip("_") or f"video_{index:04d}"
    output_path = cache_dir / f"{index:04d}_{safe_stem}_normv1_{int(width)}x{int(height)}.mp4"
    if (
        is_output_fresh(output_path, [video_path])
        and probe_video_size(output_path) == (width, height)
        and probe_duration(output_path) > 0
    ):
        return output_path
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=24,settb=AVTB,"
        "setpts=N/(24*TB),format=yuv420p"
    )
    run_process(
        [
            *ffmpeg_base_args(),
            "-i",
            str(video_path.resolve()),
            "-an",
            "-vf",
            vf,
            *common_video_output_args(output_path.name, width, height),
        ],
        cwd=cache_dir,
    )
    return output_path


def render_background_with_audio(
    videos: list[Path],
    audio_path: Path,
    subtitle_path: Path,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    layout: str,
    character_path: Path | None,
    remove_logo: bool = True,
    allow_loop: bool = False,
    story_overlay_images: list[Path] | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_filter = ""
    if subtitle_path.exists() and subtitle_path.read_text(encoding="utf-8", errors="ignore").strip():
        subtitle_name = subtitle_path.with_suffix(".ass").name if subtitle_path.with_suffix(".ass").exists() else subtitle_path.name
        subtitle_filter = f",subtitles={subtitle_name}"

    _log(log, f"Dang render video final bang FFmpeg... encoder={selected_video_encoder()}")
    validate_visual_duration(videos, duration, allow_loop=allow_loop)
    if layout == "character_drama":
        render_character_drama(
            videos,
            audio_path,
            output_path,
            duration,
            width,
            height,
            subtitle_filter,
            character_path,
            remove_logo,
            allow_loop,
            story_overlay_images=story_overlay_images or [],
        )
    elif layout == "split_drama":
        render_split_drama(videos, audio_path, output_path, duration, width, height, subtitle_filter, remove_logo, allow_loop)
    else:
        concat_path = output_path.parent / "video_concat_loop.txt"
        video_input_args = video_input_args_for_timeline(videos, duration, concat_path, allow_loop=allow_loop)
        video_filter = f"{background_video_filter(videos[0], width, height, remove_logo=remove_logo)}{subtitle_filter}"
        run_process(
            [
                *ffmpeg_base_args(),
                *video_input_args,
                "-i",
                audio_path.name,
                "-t",
                f"{duration:.3f}",
                "-vf",
                video_filter,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                *common_output_args(output_path.name, width, height),
            ],
            cwd=output_path.parent,
        )


def render_character_drama(
    videos: list[Path],
    audio_path: Path,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    subtitle_filter: str,
    character_path: Path | None,
    remove_logo: bool = True,
    allow_loop: bool = False,
    story_overlay_images: list[Path] | None = None,
) -> None:
    if not character_path or not character_path.exists():
        raise RuntimeError("Thieu anh nhan vat. Chon anh 9:16 JPG/PNG/WebP truoc khi auto edit.")

    concat_path = output_path.parent / "video_concat_loop.txt"
    video_input_args = video_input_args_for_timeline(videos, duration, concat_path, allow_loop=allow_loop)

    right_w = int(width * 0.40)
    prepared_character = prepare_character_panel(character_path, output_path.parent, right_w, height)
    left_w = width - right_w
    overlay_sequence = prepare_story_overlay_sequence(
        story_overlay_images or [],
        output_path.parent,
        left_w,
        height,
        duration,
    )
    wave_w = max(104, int(width * 0.055))
    wave_steps = max(260, height // 3)
    divider_x = max(0, width - right_w)
    divider_w = max(2, width // 640)
    wave_x = max(0, divider_x - wave_w // 2)
    char_margin = max(0, int(width * 0.008))
    bg_filter = background_video_filter(videos[0], width, height, remove_logo=remove_logo)
    if overlay_sequence:
        overlay_w, overlay_h = story_overlay_size(left_w, height)
        overlay_x = max(24, int(left_w * 0.035))
        overlay_y = max(28, int(height * 0.055))
        audio_index = 3
        filter_complex = (
            f"[0:v]{bg_filter}[bg];"
            "[1:v]format=yuv420p[char];"
            f"[2:v]scale={overlay_w}:{overlay_h}:force_original_aspect_ratio=increase,"
            f"crop={overlay_w}:{overlay_h},setsar=1,format=yuva420p,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.34:t={max(4, width // 360)}[story];"
            f"[{audio_index}:a]volume=2.45,showwaves=s={wave_steps}x{wave_w}:mode=cline:draw=full:colors=0xFFFFFF@0.98:scale=lin,"
            f"format=rgba,colorkey=0x000000:0.03:0.0,lutrgb=r=255:g=255:b=255,transpose=1,"
            f"scale={wave_w}:{height}:flags=neighbor[wave];"
            f"[bg][story]overlay=x={overlay_x}:y={overlay_y}:eof_action=repeat:shortest=0[stage_story];"
            f"[stage_story]drawbox=x={width - right_w}:y=0:w={right_w}:h={height}:color=black@0.08:t=fill[stage0];"
            f"[stage0][char]overlay=x=W-w-{char_margin}:y=0:eof_action=repeat:shortest=0[stage1a];"
            f"[stage1a]drawbox=x={divider_x}:y=0:w={divider_w}:h={height}:color=white@0.40:t=fill[stage1];"
            f"[stage1][wave]overlay=x={wave_x}:y=0:eof_action=repeat:shortest=0{subtitle_filter}[v]"
        )
        args = [
            *ffmpeg_base_args(),
            *video_input_args,
            "-framerate",
            "24",
            "-loop",
            "1",
            "-i",
            prepared_character.name,
            "-stream_loop",
            "-1",
            "-i",
            overlay_sequence.name,
            "-i",
            audio_path.name,
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            f"{audio_index}:a:0",
            *common_output_args(output_path.name, width, height),
        ]
    else:
        audio_index = 2
        filter_complex = (
            f"[0:v]{bg_filter}[bg];"
            "[1:v]format=yuv420p[char];"
            f"[{audio_index}:a]volume=2.45,showwaves=s={wave_steps}x{wave_w}:mode=cline:draw=full:colors=0xFFFFFF@0.98:scale=lin,"
            f"format=rgba,colorkey=0x000000:0.03:0.0,lutrgb=r=255:g=255:b=255,transpose=1,"
            f"scale={wave_w}:{height}:flags=neighbor[wave];"
            f"[bg]drawbox=x={width - right_w}:y=0:w={right_w}:h={height}:color=black@0.08:t=fill[stage0];"
            f"[stage0][char]overlay=x=W-w-{char_margin}:y=0:eof_action=repeat:shortest=0[stage1a];"
            f"[stage1a]drawbox=x={divider_x}:y=0:w={divider_w}:h={height}:color=white@0.40:t=fill[stage1];"
            f"[stage1][wave]overlay=x={wave_x}:y=0:eof_action=repeat:shortest=0{subtitle_filter}[v]"
        )
        args = [
            *ffmpeg_base_args(),
            *video_input_args,
            "-framerate",
            "24",
            "-loop",
            "1",
            "-i",
            prepared_character.name,
            "-i",
            audio_path.name,
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            f"{audio_index}:a:0",
            *common_output_args(output_path.name, width, height),
        ]
    run_process(args, cwd=output_path.parent)


def render_split_drama(
    videos: list[Path],
    audio_path: Path,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    subtitle_filter: str,
    remove_logo: bool = True,
    allow_loop: bool = False,
) -> None:
    left_videos = videos[::2] or videos
    right_videos = videos[1::2] or videos
    left_concat = output_path.parent / "video_concat_left.txt"
    right_concat = output_path.parent / "video_concat_right.txt"
    left_sequence = repeated_video_list(left_videos, duration) if allow_loop else left_videos
    right_sequence = repeated_video_list(right_videos, duration) if allow_loop else right_videos
    write_video_concat_file(left_sequence, left_concat)
    write_video_concat_file(right_sequence, right_concat)

    left_w = int(width * 0.67)
    right_w = width - left_w
    divider_w = max(4, width // 360)
    line_x = left_w - divider_w // 2
    left_filter = cover_video_filter(left_w, height, remove_logo=remove_logo)
    right_filter = cover_video_filter(right_w, height, remove_logo=remove_logo)
    filter_complex = (
        f"[0:v]{left_filter}[left];"
        f"[1:v]{right_filter}[right];"
        f"color=c=black:s={width}x{height}:d={duration:.3f}[base];"
        f"[base][left]overlay=0:0[tmp1];"
        f"[tmp1][right]overlay={left_w}:0[tmp2];"
        f"[tmp2]drawbox=x={line_x}:y=0:w={divider_w}:h={height}:color=white@0.72:t=fill,"
        f"drawbox=x={left_w - max(14, width // 90)}:y={int(height * 0.50)}:"
        f"w={max(28, width // 45)}:h={max(3, height // 190)}:color=white@0.85:t=fill,"
        f"drawbox=x={left_w - max(20, width // 70)}:y={int(height * 0.62)}:"
        f"w={max(40, width // 36)}:h={max(3, height // 190)}:color=white@0.78:t=fill"
        f"{subtitle_filter}[v]"
    )
    run_process(
        [
            *ffmpeg_base_args(),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            left_concat.name,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            right_concat.name,
            "-i",
            audio_path.name,
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "2:a:0",
            *common_output_args(output_path.name, width, height),
        ],
        cwd=output_path.parent,
    )


def common_output_args(output_name: str, width: int, height: int) -> list[str]:
    return [
        "-r",
        "24",
        *video_encoder_args(width, height),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_name,
    ]


def common_video_output_args(output_name: str, width: int, height: int) -> list[str]:
    return [
        "-r",
        "24",
        *video_encoder_args(width, height),
        output_name,
    ]


def video_encoder_args(width: int, height: int) -> list[str]:
    return video_encoder_args_for(selected_video_encoder(), width, height)


def video_encoder_args_for(encoder: str, width: int, height: int) -> list[str]:
    bitrate = target_video_bitrate(width, height)
    if encoder == "h264_amf":
        return [
            "-c:v",
            "h264_amf",
            "-usage",
            "transcoding",
            "-quality",
            "speed",
            "-rc",
            "vbr_peak",
            "-b:v",
            bitrate,
            "-maxrate",
            scale_bitrate(bitrate, 1.6),
            "-bufsize",
            scale_bitrate(bitrate, 3.0),
            "-async_depth",
            "4",
            "-pix_fmt",
            "yuv420p",
        ]
    if encoder == "h264_mf":
        return [
            "-c:v",
            "h264_mf",
            "-hw_encoding",
            "1",
            "-rate_control",
            "pc_vbr",
            "-b:v",
            bitrate,
            "-pix_fmt",
            "yuv420p",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "0",
    ]


def selected_video_encoder() -> str:
    global _ENCODER_CACHE
    if _ENCODER_CACHE:
        return _ENCODER_CACHE
    for encoder in ("h264_amf", "h264_mf", "libx264"):
        if ffmpeg_encoder_works(encoder):
            _ENCODER_CACHE = encoder
            return encoder
    _ENCODER_CACHE = "libx264"
    return _ENCODER_CACHE


def ffmpeg_encoder_works(encoder: str) -> bool:
    try:
        proc = subprocess.run(
            [
                ffmpeg_bin(),
                "-hide_banner",
                "-nostats",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=s=128x128:d=0.1:r=24",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **_win_hidden_kwargs(),
        )
        return proc.returncode == 0
    except Exception:
        return False


def target_video_bitrate(width: int, height: int) -> str:
    pixels = max(1, int(width) * int(height))
    if pixels <= 1280 * 720:
        return "3500k"
    if pixels <= 1920 * 1080:
        return "6000k"
    if pixels <= 2560 * 1440:
        return "10000k"
    return "18000k"


def scale_bitrate(value: str, multiplier: float) -> str:
    number = int(re.sub(r"\D", "", value) or "0")
    suffix = re.sub(r"[\d.]", "", value) or "k"
    return f"{max(1, int(number * multiplier))}{suffix}"


def _setting_bool(settings: dict, key: str, default: bool = False) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "bat", "bật", "co", "có"}:
        return True
    if text in {"0", "false", "no", "n", "off", "tat", "tắt", "khong", "không"}:
        return False
    return bool(default)


def background_video_filter(source: Path, width: int, height: int, remove_logo: bool = True) -> str:
    if not remove_logo:
        source_size = probe_video_size(source)
        if source_size == (width, height):
            return "setsar=1"
    return cover_video_filter(width, height, remove_logo=remove_logo)


def cover_video_filter(width: int, height: int, remove_logo: bool = True) -> str:
    filters = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    if remove_logo:
        filters = f"{filters},{veo_logo_filter(width, height)}"
    return filters


def veo_logo_filter(width: int, height: int) -> str:
    box_w = max(72, int(width * 0.16))
    box_h = max(34, int(height * 0.085))
    pad_x = max(8, int(width * 0.012))
    pad_y = max(8, int(height * 0.018))
    x = max(0, width - box_w - pad_x)
    y = max(0, height - box_h - pad_y)
    return f"delogo=x={x}:y={y}:w={box_w}:h={box_h}:show=0"


def resolve_character_path(project: Path, settings: dict) -> Path | None:
    raw = str(settings.get("final_video_character_path") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = project / path
        if path.exists():
            return path
    if raw:
        return Path(raw)
    try:
        data = json.loads((project / "input.json").read_text(encoding="utf-8"))
        for key in ("character_image_path", "character_path", "final_video_character_path"):
            value = str(data.get(key) or "").strip()
            if value:
                path = Path(value)
                if not path.is_absolute():
                    path = project / path
                if path.exists():
                    return path
    except Exception:
        pass
    for name in ("character.png", "character.webp", "character.jpg", "character.jpeg"):
        path = project / "assets" / name
        if path.exists():
            return path
        path = project / name
        if path.exists():
            return path
    return None


def prepare_character_panel(character_path: Path, output_dir: Path, width: int, height: int) -> Path:
    output_path = output_dir / f"_character_panel_{width}x{height}.png"
    if is_output_fresh(output_path, [character_path]) and probe_video_size(output_path) == (width, height):
        return output_path
    run_process(
        [
            *ffmpeg_base_args(),
            "-i",
            str(character_path),
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(output_path),
        ]
    )
    return output_path


def story_overlay_size(left_width: int, height: int) -> tuple[int, int]:
    overlay_w = max(360, int(left_width * 0.58))
    overlay_w = min(overlay_w, max(360, left_width - 96))
    overlay_h = int(round(overlay_w * 9 / 16))
    max_h = int(height * 0.46)
    if overlay_h > max_h:
        overlay_h = max(220, max_h)
        overlay_w = int(round(overlay_h * 16 / 9))
    return even_dimension(overlay_w), even_dimension(overlay_h)


def even_dimension(value: int) -> int:
    return max(2, int(value) - (int(value) % 2))


def prepare_story_overlay_sequence(
    images: list[Path],
    output_dir: Path,
    left_width: int,
    height: int,
    duration: float,
) -> Path | None:
    image_files = [path for path in images if path.exists() and path.suffix.lower() in IMAGE_SUFFIXES]
    if not image_files:
        return None
    overlay_w, overlay_h = story_overlay_size(left_width, height)
    target_duration = max(0.05, float(duration or 0.0))
    duration_ms = int(round(target_duration * 1000))
    sequence_path = output_dir / f"_story_overlay_sequence_staticv4_{overlay_w}x{overlay_h}_{len(image_files)}_{duration_ms}ms.mp4"
    existing_duration = probe_duration(sequence_path) if is_output_fresh(sequence_path, image_files) else 0.0
    if existing_duration + 0.25 >= target_duration:
        return sequence_path

    per_image = target_duration / max(1, len(image_files))
    image_durations = frame_safe_durations([per_image for _ in image_files], target_duration)
    concat_path = output_dir / "_story_overlay_concat.txt"
    write_image_concat_file(image_files, image_durations, concat_path)
    vf = (
        f"scale={overlay_w}:{overlay_h}:force_original_aspect_ratio=increase,"
        f"crop={overlay_w}:{overlay_h},setsar=1,fps=24,settb=AVTB,"
        "setpts=N/(24*TB),format=yuv420p"
    )
    run_process(
        [
            *ffmpeg_base_args(),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_path.name,
            "-t",
            f"{target_duration:.3f}",
            "-an",
            "-vf",
            vf,
            *common_video_output_args(sequence_path.name, overlay_w, overlay_h),
        ],
        cwd=output_dir,
    )
    return sequence_path


def write_subtitles_for_project(
    scripts: list[Path],
    voices: list[Path],
    subtitle_path: Path,
    total_duration: float,
    width: int = 1920,
    height: int = 1080,
    settings: dict | None = None,
) -> None:
    settings = settings or {}
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_durations = [probe_duration(path) for path in voices]
    if not any(d > 0 for d in chapter_durations):
        chapter_durations = []
    base_speed = _setting_float(settings, "text_to_voice_speed", 1.0, 0.5, 2.0)
    delivery = str(settings.get("text_to_voice_delivery") or "dramatic")

    entries: list[tuple[float, float, str]] = []
    cursor = 0.0
    count = min(len(scripts), len(voices)) if scripts else 0
    for idx in range(count):
        script_text = scripts[idx].read_text(encoding="utf-8", errors="ignore")
        chapter_duration = chapter_durations[idx] if idx < len(chapter_durations) and chapter_durations[idx] > 0 else total_duration / max(1, count)
        timing_segments = read_voice_timing_segments(voices[idx])
        if timing_segments:
            entries.extend(timed_caption_entries_from_segments(timing_segments, cursor))
        else:
            entries.extend(timed_caption_entries(script_text, cursor, chapter_duration, base_speed=base_speed, delivery=delivery))
        cursor += chapter_duration

    if not entries and scripts:
        all_text = "\n\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in scripts)
        entries = timed_caption_entries(all_text, 0.0, total_duration, base_speed=base_speed, delivery=delivery)

    entries = shift_subtitles_earlier(entries, SUBTITLE_ADVANCE_SECONDS)

    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        if not text.strip():
            continue
        lines.append(str(index))
        lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        lines.append(wrap_caption(text, width=SUBTITLE_WRAP_WIDTH))
        lines.append("")
    subtitle_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    write_ass_subtitles(entries, subtitle_path.with_suffix(".ass"), width=width, height=height)


def shift_subtitles_earlier(entries: list[tuple[float, float, str]], seconds: float) -> list[tuple[float, float, str]]:
    advance = max(0.0, float(seconds or 0.0))
    if advance <= 0:
        return entries
    shifted: list[tuple[float, float, str]] = []
    for start, end, text in entries:
        new_start = max(0.0, start - advance)
        new_end = max(new_start + 0.2, end - advance)
        shifted.append((new_start, new_end, text))
    return shifted


def write_ass_subtitles(entries: list[tuple[float, float, str]], output_path: Path, width: int, height: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font_size = max(27, min(42, int(height * 0.032) + 3))
    margin_l = max(42, int(width * 0.075))
    margin_r = max(42, int(width * 0.40))
    margin_v = max(38, int(height * 0.072))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: LeftSmall,Arial,{font_size},&H00FFFFFF,&H000000FF,&H8A000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,1,{margin_l},{margin_r},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in entries:
        caption = escape_ass_text(wrap_caption(text, width=SUBTITLE_WRAP_WIDTH))
        if caption.strip():
            lines.append(
                f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},LeftSmall,,0,0,0,,{caption}"
            )
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def timed_caption_entries(
    text: str,
    offset: float,
    duration: float,
    base_speed: float = 1.0,
    delivery: str = "dramatic",
) -> list[tuple[float, float, str]]:
    segments = build_delivery_segments(clean_script_text(text, keep_paragraphs=True), base_speed, delivery)
    if not segments or duration <= 0:
        return []
    total_pause = sum(pause for _text, pause, _speed in segments)
    speech_budget = max(duration * 0.72, duration - total_pause)
    segment_weights = [subtitle_speech_weight(segment_text) / max(0.5, speed) for segment_text, _pause, speed in segments]
    total_weight = float(sum(segment_weights) or 1)
    cursor = offset
    entries: list[tuple[float, float, str]] = []
    chapter_end = offset + duration
    for (segment_text, pause, _speed), weight in zip(segments, segment_weights):
        remaining = max(0.0, chapter_end - cursor)
        if remaining <= 0:
            break
        segment_speech = min(remaining, max(0.35, speech_budget * (float(weight) / total_weight)))
        entries.extend(caption_entries_for_segment(segment_text, cursor, segment_speech))
        cursor = min(chapter_end, cursor + segment_speech + pause)
    if entries:
        start, _end, caption = entries[-1]
        entries[-1] = (start, min(chapter_end, max(_end, start + 0.2)), caption)
    return entries


def timed_caption_entries_from_segments(segments: list[dict], offset: float) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    for segment in segments:
        text = clean_caption_text(str(segment.get("text") or ""))
        if not text:
            continue
        try:
            start = offset + max(0.0, float(segment.get("start") or 0.0))
            end = offset + max(0.0, float(segment.get("end") or 0.0))
        except Exception:
            continue
        if end <= start:
            continue
        entries.extend(caption_entries_for_segment(text, start, end - start))
    return entries


def read_voice_timing_segments(voice_path: Path) -> list[dict]:
    timing_path = voice_path.with_suffix(".segments.json")
    if not timing_path.exists():
        return []
    try:
        data = json.loads(timing_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list):
        return []
    return [segment for segment in segments if isinstance(segment, dict)]


def count_voice_timing_files(voices: list[Path]) -> int:
    return sum(1 for voice in voices if read_voice_timing_segments(voice))


def caption_entries_for_segment(text: str, start: float, duration: float) -> list[tuple[float, float, str]]:
    captions = [clean_caption_text(caption) for caption in pack_captions(split_caption_units(text), max_chars=SUBTITLE_MAX_CHARS)]
    captions = [caption for caption in captions if caption]
    if not captions:
        return []
    weights = [subtitle_speech_weight(caption) for caption in captions]
    total_weight = float(sum(weights) or 1)
    cursor = start
    end_limit = start + max(0.2, duration)
    entries: list[tuple[float, float, str]] = []
    for caption, weight in zip(captions, weights):
        span = max(0.32, duration * (float(weight) / total_weight))
        end = min(end_limit, cursor + span)
        if end <= cursor:
            end = min(end_limit, cursor + 0.32)
        entries.append((cursor, end, caption))
        cursor = end
    return entries


def build_delivery_segments(text: str, base_speed: float, delivery: str) -> list[tuple[str, float, float]]:
    style = DELIVERY_STYLES.get(delivery, DELIVERY_STYLES["natural"])
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalize_subtitle_source(text).strip()) if part.strip()]
    if not paragraphs:
        return []

    segments: list[tuple[str, float, float]] = []
    for paragraph in paragraphs:
        sentences = split_delivery_sentences(paragraph)
        if not sentences:
            continue
        inside_dialogue = False
        for index, sentence in enumerate(sentences):
            quote_count = dialogue_quote_count(sentence)
            segment_text = clean_caption_text(sentence)
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
        last_text, _pause, last_speed = segments[-1]
        segments[-1] = (last_text, 0.0, last_speed)
    return segments


def split_delivery_sentences(paragraph: str) -> list[str]:
    paragraph = re.sub(r"\s+", " ", str(paragraph or "")).strip()
    if not paragraph:
        return []
    sentences = re.findall(r"[^.!?;:]+(?:[.!?;:]+[\"'“”‘’)\]]*)?|[^.!?;:]+$", paragraph)
    pieces: list[str] = []
    for sentence in sentences:
        pieces.extend(split_long_delivery_sentence(sentence))
    return [piece.strip() for piece in pieces if piece.strip()]


def split_long_delivery_sentence(sentence: str, max_chars: int = 430) -> list[str]:
    sentence = str(sentence or "").strip()
    if len(sentence) <= max_chars:
        return [sentence] if sentence else []
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


def segment_speed(text: str, base_speed: float, style: dict[str, float], is_dialogue: bool = False) -> float:
    multiplier = float(style["speedBias"])
    clean = str(text or "").strip()
    word_count = len(clean.split())
    if clean.endswith("?"):
        multiplier *= float(style["questionSpeed"])
    elif clean.endswith("!"):
        multiplier *= float(style["exclaimSpeed"])
    if is_dialogue:
        multiplier *= float(style.get("dialogueSpeed", 0.97))
    if word_count <= 6:
        multiplier *= float(style["shortSpeed"])
    return clamp_float(base_speed * multiplier, 0.5, 2.0)


def dialogue_quote_count(text: str) -> int:
    value = str(text or "")
    return value.count('"') + value.count("\u201c") + value.count("\u201d")


def sentence_pause(text: str, base_pause: float, style: dict[str, float], is_dialogue: bool) -> float:
    clean = str(text or "").strip()
    pause = float(base_pause)
    if is_dialogue:
        pause *= float(style.get("dialoguePause", 1.0))
    if len(clean.split()) <= 4:
        pause *= float(style.get("punchlinePause", 1.0))
    if clean.endswith("?"):
        pause *= 1.12
    return min(pause, float(style.get("maxPause", 0.55)))


def split_caption_units(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    units = [part.strip() for part in re.split(r"(?<=[,;:])\s+", text) if part.strip()]
    if len(units) <= 1:
        units = [text]
    result: list[str] = []
    for unit in units:
        if len(unit) <= SUBTITLE_MAX_CHARS:
            result.append(unit)
            continue
        words = unit.split()
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if current and len(candidate) > SUBTITLE_MAX_CHARS:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
    return result


def normalize_subtitle_source(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\b([AaPp])\.\s*[Mm]\.", lambda match: f"{match.group(1).lower()}m", value)
    value = re.sub(r"\b([AaPp])\s+[Mm]\.", lambda match: f"{match.group(1).lower()}m", value)
    return value


def clean_caption_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = value.strip(" \"'“”‘’.,;:")
    value = value.translate(str.maketrans("", "", "\"'“”‘’"))
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = value.strip(" ,;:")
    if not re.search(r"[\w\d]", value, flags=re.UNICODE):
        return ""
    return value


def subtitle_speech_weight(text: str) -> float:
    clean = str(text or "").strip()
    words = clean.split()
    punctuation = len(re.findall(r"[,;:]", clean)) * 0.45 + len(re.findall(r"[.!?]", clean)) * 0.75
    digits = len(re.findall(r"\d+", clean)) * 0.4
    return max(1.0, len(words) + len(clean) * 0.025 + punctuation + digits)


def pack_captions(sentences: list[str], max_chars: int = 92) -> list[str]:
    captions: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not current:
            current = sentence
            continue
        merged = f"{current} {sentence}"
        if len(merged) <= max_chars:
            current = merged
        else:
            captions.append(current)
            current = sentence
    if current:
        captions.append(current)
    return captions


def clean_script_text(text: str, keep_paragraphs: bool = False) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            if keep_paragraphs and current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        lowered = line.lower()
        if lowered.startswith(("chapter ", "title:", "word count:", "voiceover:", "script:")):
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    if keep_paragraphs:
        return "\n\n".join(paragraphs)
    return " ".join(paragraphs)


def wrap_caption(text: str, width: int = 42) -> str:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return "\n".join(lines)
    mid = math.ceil(len(lines) / 2)
    return "\n".join([" ".join(lines[:mid]), " ".join(lines[mid:])])


def repeated_video_list(videos: list[Path], target_duration: float) -> list[Path]:
    durations = [probe_duration(path) for path in videos]
    cycle_duration = sum(d for d in durations if d > 0)
    if cycle_duration <= 0:
        cycle_duration = float(len(videos) * 8)
    safety = max(600.0, float(target_duration or 0.0) * 0.50)
    repeats = max(1, int(math.ceil((float(target_duration or 0.0) + safety) / cycle_duration)))
    result: list[Path] = []
    for _ in range(repeats):
        result.extend(videos)
    return result


def looped_video_input_args(videos: list[Path], target_duration: float, concat_path: Path) -> list[str]:
    if len(videos) == 1:
        return ["-stream_loop", "-1", "-i", str(videos[0].resolve())]
    write_video_concat_file(repeated_video_list(videos, target_duration), concat_path)
    return ["-f", "concat", "-safe", "0", "-i", concat_path.name]


def video_input_args_for_timeline(videos: list[Path], target_duration: float, concat_path: Path, allow_loop: bool = False) -> list[str]:
    if allow_loop:
        return looped_video_input_args(videos, target_duration, concat_path)
    validate_visual_duration(videos, target_duration, allow_loop=False)
    write_video_concat_file(videos, concat_path)
    return ["-f", "concat", "-safe", "0", "-i", concat_path.name]


def validate_visual_duration(videos: list[Path], target_duration: float, allow_loop: bool = False) -> None:
    if allow_loop:
        return
    durations = [probe_duration(path) for path in videos]
    total = sum(d for d in durations if d > 0)
    target = max(0.0, float(target_duration or 0.0))
    if target <= 0 or total + 0.25 >= target:
        return
    missing = max(0.0, target - total)
    needed_veo = int(math.ceil(missing / 8.0))
    raise RuntimeError(
        "Visual media khong du do dai voice va che do loop dang tat. "
        f"Voice={format_elapsed(target)}, media={format_elapsed(total)}, thieu={format_elapsed(missing)}. "
        f"Can them khoang {needed_veo} clip VEO 8s nua, hoac bat final_video_allow_loop=true neu chap nhan lap."
    )


def probe_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    stat = path.stat()
    cache_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _DURATION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **_win_hidden_kwargs(),
        )
        if proc.returncode != 0:
            return 0.0
        value = max(0.0, float(str(proc.stdout or "0").strip()))
        _DURATION_CACHE[cache_key] = value
        return value
    except Exception:
        return 0.0


def probe_video_size(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    cache_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _VIDEO_INFO_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **_win_hidden_kwargs(),
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return None
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
        if width <= 0 or height <= 0:
            return None
        value = (width, height)
        _VIDEO_INFO_CACHE[cache_key] = value
        return value
    except Exception:
        return None


def write_concat_file(paths: list[Path], output_path: Path) -> None:
    lines = []
    for path in paths:
        text = path.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{text}'")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_video_concat_file(paths: list[Path], output_path: Path) -> None:
    lines = ["ffconcat version 1.0"]
    for path in paths:
        text = path.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{text}'")
        duration = probe_duration(path)
        if duration > 0:
            lines.append(f"duration {duration:.6f}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_image_concat_file(paths: list[Path], durations: list[float], output_path: Path) -> None:
    lines = ["ffconcat version 1.0"]
    last_text = ""
    for index, path in enumerate(paths):
        text = path.resolve().as_posix().replace("'", r"'\''")
        last_text = text
        duration = float(durations[index] if index < len(durations) else 0.0)
        lines.append(f"file '{text}'")
        lines.append(f"duration {max(0.05, duration):.6f}")
    if last_text:
        lines.append(f"file '{last_text}'")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_output_fresh(output_path: Path, inputs: list[Path]) -> bool:
    if not output_path.exists() or not inputs:
        return False
    try:
        output_mtime = output_path.stat().st_mtime_ns
        return all(path.exists() and output_mtime >= path.stat().st_mtime_ns for path in inputs)
    except OSError:
        return False


def run_process(args: list[str], cwd: Path | None = None) -> None:
    current_args = args
    tried_encoders: set[str] = set()
    proc: subprocess.CompletedProcess[str] | None = None
    for _ in range(3):
        proc = run_subprocess(current_args, cwd)
        if proc.returncode == 0:
            return
        encoder = current_encoder_from_args(current_args)
        if encoder:
            tried_encoders.add(encoder)
        retry_args = encoder_fallback_args(current_args, tried_encoders)
        if not retry_args:
            break
        current_args = retry_args

    assert proc is not None
    tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-25:])
    raise RuntimeError(tail or f"Lenh loi voi ma {proc.returncode}: {' '.join(args)}")


def run_subprocess(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_win_hidden_kwargs(),
    )


def encoder_fallback_args(args: list[str], tried_encoders: set[str]) -> list[str] | None:
    current = current_encoder_from_args(args)
    if not current:
        return None
    candidates = ["h264_mf", "libx264"] if current == "h264_amf" else ["libx264"]
    next_encoder = None
    for candidate in candidates:
        if candidate in tried_encoders:
            continue
        if candidate == "libx264" or ffmpeg_encoder_works(candidate):
            next_encoder = candidate
            break
    if not next_encoder:
        return None
    width, height = output_size_from_args(args)
    retry = replace_video_encoder_args(args, video_encoder_args_for(next_encoder, width, height))
    if retry:
        global _ENCODER_CACHE
        _ENCODER_CACHE = next_encoder
    return retry


def current_encoder_from_args(args: list[str]) -> str | None:
    for index, value in enumerate(args[:-1]):
        if value == "-c:v":
            return args[index + 1]
    return None


def output_size_from_args(args: list[str]) -> tuple[int, int]:
    text = " ".join(args)
    for match in re.finditer(r"(?:scale|crop)=(\d+)[:x](\d+)", text):
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            return width, height
    for match in re.finditer(r"color=[^,;]*:s=(\d+)x(\d+)", text):
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            return width, height
    return 1920, 1080


def replace_video_encoder_args(args: list[str], replacement: list[str]) -> list[str] | None:
    try:
        start = args.index("-c:v")
    except ValueError:
        return None
    end = len(args) - 1
    for index in range(start + 2, len(args)):
        if args[index] in {"-c:a", "-shortest"}:
            end = index
            break
    return [*args[:start], *replacement, *args[end:]]


def ffmpeg_base_args() -> list[str]:
    return [ffmpeg_bin(), "-hide_banner", "-nostats", "-loglevel", "error", "-y"]


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def format_srt_time(value: float) -> str:
    total_ms = max(0, int(round(float(value or 0.0) * 1000)))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    seconds = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_ass_time(value: float) -> str:
    total_cs = max(0, int(round(float(value or 0.0) * 100)))
    hours = total_cs // 360_000
    total_cs %= 360_000
    minutes = total_cs // 6_000
    total_cs %= 6_000
    seconds = total_cs // 100
    centis = total_cs % 100
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def escape_ass_text(text: str) -> str:
    cleaned = str(text or "").replace("{", "(").replace("}", ")")
    return cleaned.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")


def format_elapsed(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0.0))))
    hours = total // 3600
    total %= 3600
    minutes = total // 60
    seconds = total % 60
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _setting_int(settings: dict, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(settings.get(key) or default))
        return max(minimum, min(maximum, value))
    except Exception:
        return default


def _setting_float(settings: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(settings.get(key) or default))
        return clamp_float(value, minimum, maximum)
    except Exception:
        return default


def clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _log(callback: Callable[[str], None] | None, message: str) -> None:
    if callable(callback):
        callback(message)
