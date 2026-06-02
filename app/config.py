from __future__ import annotations

import json
import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = APP_DIR / "settings.json"
UI_STATE_PATH = APP_DIR / "ui_state.json"
DEPRECATED_SETTING_KEYS = {
    "voice_python",
    "voice_profiles_path",
    "default_voice_style",
    "default_voice_speed",
}
DEPRECATED_SETTING_PREFIXES = ("cap" + "cut_",)


DEFAULT_SETTINGS = {
    "projects_dir": str(APP_DIR / "Projects"),
    "workflow_path": str(APP_DIR / "workflows" / "fast_story_8_chapters.json"),
    "chrome_profile_root": str(APP_DIR / "chrome_user_data_chatgpt"),
    "chrome_profile_name": "PROFILE_1",
    "chrome_cdp_host": "127.0.0.1",
    "chrome_cdp_port": 9444,
    "chatgpt_url": "https://chatgpt.com/",
    "text_to_voice_root": "kokoro-tts-local",
    "text_to_voice_python": "",
    "text_to_voice_host": "127.0.0.1",
    "text_to_voice_port": 7860,
    "text_to_voice_language": "a",
    "text_to_voice_voice": "af_heart",
    "text_to_voice_delivery": "dramatic",
    "text_to_voice_speed": 1.0,
    "text_to_voice_max_chars": 10000,
    "text_to_voice_timeout": 1800,
    "text_to_voice_parallel_jobs": 1,
    "chrome_exe_path": "",
    "veo3_root": "veo3-local",
    "veo3_email": "",
    "veo3_password": "",
    "veo3_profile_name": "PROFILE_1",
    "veo3_aspect_ratio": "16:9",
    "veo3_model": "Veo 3.1 - Fast",
    "create_image_model": "Nano Banana pro",
    "veo3_output_count": 1,
    "veo3_multi_video": 3,
    "veo3_wait_gen_video": 12,
    "veo3_retry_with_error": 5,
    "veo3_wait_resend_video": 30,
    "veo3_token_retry": 5,
    "veo3_token_retry_delay": 3,
    "veo3_download_images": True,
    "veo_character_consistency": True,
    "final_video_width": 1920,
    "final_video_height": 1080,
    "final_video_layout": "character_drama",
    "background_video_mode": "cooking",
    "final_video_character_path": "",
    "final_video_allow_loop": False,
    "final_image_duration": 18.0,
    "veo_prompt_limit": 160,
    "default_chapter_count": 8,
    "default_target_word_count": 6800,
}


APP_LOCAL_SETTING_KEYS = {
    "projects_dir",
    "workflow_path",
    "chrome_profile_root",
    "text_to_voice_root",
    "text_to_voice_python",
    "veo3_root",
    "final_video_character_path",
}


def rebase_app_path(value: str | Path, require_exists: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw

    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        return raw

    try:
        if path.resolve(strict=False) == APP_DIR.resolve(strict=False) or path.resolve(strict=False).is_relative_to(APP_DIR.resolve(strict=False)):
            return str(path)
    except Exception:
        pass

    parts = list(path.parts)
    app_name = APP_DIR.name.lower()
    for index in range(len(parts) - 1, -1, -1):
        if str(parts[index]).lower() != app_name:
            continue
        suffix_parts = parts[index + 1 :]
        candidate = APP_DIR.joinpath(*suffix_parts) if suffix_parts else APP_DIR
        if not require_exists or candidate.exists():
            return str(candidate)
    return raw


def _normalize_app_local_paths(settings: dict) -> bool:
    changed = False
    for key in APP_LOCAL_SETTING_KEYS:
        raw = settings.get(key)
        if not raw:
            continue
        rebased = rebase_app_path(raw, require_exists=True)
        if rebased != str(raw):
            settings[key] = rebased
            changed = True

    workflow = Path(str(settings.get("workflow_path") or ""))
    default_workflow = Path(DEFAULT_SETTINGS["workflow_path"])
    if not workflow.exists() and default_workflow.exists():
        settings["workflow_path"] = str(default_workflow)
        changed = True

    for key in ("projects_dir", "chrome_profile_root"):
        raw = str(settings.get(key) or "").strip()
        if not raw:
            settings[key] = DEFAULT_SETTINGS[key]
            changed = True
            continue
        path = Path(raw)
        default_path = Path(DEFAULT_SETTINGS[key])
        if not path.exists() and default_path.exists():
            settings[key] = str(default_path)
            changed = True

    return changed


def _write_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_settings() -> dict:
    data = {}
    try:
        if SETTINGS_PATH.exists():
            parsed = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, dict):
                data = parsed
    except Exception:
        data = {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    changed = _normalize_app_local_paths(merged)
    try:
        max_chars = int(str(merged.get("text_to_voice_max_chars") or 0))
        merged["text_to_voice_max_chars"] = max(1000, min(max_chars, 12000))
    except Exception:
        merged["text_to_voice_max_chars"] = 10000
    try:
        parallel_jobs = int(str(merged.get("text_to_voice_parallel_jobs") or 0))
        merged["text_to_voice_parallel_jobs"] = max(1, min(parallel_jobs, 2))
    except Exception:
        merged["text_to_voice_parallel_jobs"] = 1
    for key, default in (("final_video_width", 1920), ("final_video_height", 1080)):
        try:
            value = int(str(merged.get(key) or default))
            merged[key] = max(360, min(value, 3840))
        except Exception:
            merged[key] = default
    try:
        prompt_limit = int(str(merged.get("veo_prompt_limit") or 160))
        merged["veo_prompt_limit"] = max(1, min(prompt_limit, 500))
    except Exception:
        merged["veo_prompt_limit"] = 160
    for key, default, low, high in (
        ("veo3_output_count", 1, 1, 4),
        ("veo3_multi_video", 1, 1, 20),
        ("veo3_wait_gen_video", 12, 0, 999),
        ("veo3_retry_with_error", 5, 0, 99),
        ("veo3_wait_resend_video", 30, 0, 999),
        ("veo3_token_retry", 5, 1, 20),
        ("veo3_token_retry_delay", 3, 0, 99),
    ):
        try:
            value = int(str(merged.get(key) or default))
            merged[key] = max(low, min(value, high))
        except Exception:
            merged[key] = default
    create_image_model = str(merged.get("create_image_model") or merged.get("veo3_create_image_model") or "Nano Banana pro").strip()
    valid_create_image_models = {"Nano Banana pro", "Nano Banana 2", "Nano Banana", "Imagen 4"}
    merged["create_image_model"] = create_image_model if create_image_model in valid_create_image_models else "Nano Banana pro"
    for key in DEPRECATED_SETTING_KEYS:
        merged.pop(key, None)
    for key in list(merged):
        if any(str(key).startswith(prefix) for prefix in DEPRECATED_SETTING_PREFIXES):
            merged.pop(key, None)
    ensure_dirs(merged)
    if changed:
        try:
            _write_settings(merged)
        except Exception:
            pass
    return merged


def save_settings(settings: dict) -> None:
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(settings, dict):
        merged.update(settings)
    _normalize_app_local_paths(merged)
    for key in DEPRECATED_SETTING_KEYS:
        merged.pop(key, None)
    for key in list(merged):
        if any(str(key).startswith(prefix) for prefix in DEPRECATED_SETTING_PREFIXES):
            merged.pop(key, None)
    ensure_dirs(merged)
    _write_settings(merged)


def load_ui_state() -> dict:
    try:
        if UI_STATE_PATH.exists():
            data = json.loads(UI_STATE_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_ui_state(state: dict) -> None:
    if not isinstance(state, dict):
        return
    UI_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_dirs(settings: dict) -> None:
    for key in ("projects_dir", "chrome_profile_root"):
        raw = settings.get(key)
        if raw:
            try:
                Path(raw).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass


def find_chrome_executable(custom_path: str = "") -> str:
    raw = str(custom_path or "").strip()
    if raw and Path(raw).exists():
        return raw

    candidates = [
        Path(os.getenv("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.getenv("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return ""
