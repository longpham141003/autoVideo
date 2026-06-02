from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .config import APP_DIR
from .shorts_builder import collect_image_files, distribute_short_veo_images, prepare_short_veo_prompt_file
from .veo_prompts import prepare_character_reference_prompt_file, prepare_veo_prompt_file
from .video_editor import render_project_video


def run_veo3_then_edit(project_dir: str | Path, settings: dict, log: Callable[[str], None] | None = None) -> Path:
    render_images = run_veo3_generate_only(project_dir, settings, log=log)
    project = Path(project_dir)
    _log(log, f"VEO3 da co {len(render_images)} anh visual. Bat dau auto edit.")
    result = render_project_video(project, settings, log=log, background_files=render_images)
    return result.output_path


def run_veo3_generate_only(project_dir: str | Path, settings: dict, log: Callable[[str], None] | None = None) -> list[Path]:
    project = Path(project_dir)
    root = ensure_veo3_engine(settings)
    log = _project_log_callback(project, log)
    _log(log, f"VEO3 engine: {root}")

    limit = _int_setting(settings, "veo_prompt_limit", 160, 1, 500)
    reference_prompts, reference_prompt_path = prepare_character_reference_prompt_file(
        project,
        limit=_int_setting(settings, "veo_character_reference_prompt_limit", 64, 1, 200),
    )
    prompts, prompt_path = prepare_veo_prompt_file(
        project,
        limit=limit,
        character_consistency=_bool_setting(settings, "veo_character_consistency", True),
    )
    if reference_prompts and reference_prompt_path is not None:
        _log(log, f"Da chuan bi {len(reference_prompts)} prompt reference nhan vat: {reference_prompt_path}")
    _log(log, f"Da chuan bi {len(prompts)} prompt tao anh: {prompt_path}")

    configure_veo3_for_project(settings, project)
    ensure_veo3_auth_ready(settings, log)

    video_root = project / "veo_videos"
    video_root.mkdir(parents=True, exist_ok=True)
    _log(
        log,
        "Luu y: neu Chrome token hien 'Da co loi xay ra, vui long thu lai' "
        "sau khi tu dien prompt thi do la request UI bi chan de lay token; hay xem log API ben duoi de biet loi that.",
    )
    workflow_module = importlib.import_module("A_workflow_generate_image")
    if reference_prompts:
        reference_prompt_items = _build_veo3_prompt_items(reference_prompts)
        reference_project_name = f"{project.name}_characters"
        reference_output_root = project / "character_references"
        reference_output_root.mkdir(parents=True, exist_ok=True)
        reference_project_data = {
            "prompts": {
                "text_to_video": reference_prompt_items
            },
            "_use_project_prompts": True,
            "_worker_controls_lifecycle": False,
            "aspect_ratio": str(settings.get("veo3_aspect_ratio") or "16:9"),
            "veo_model": str(settings.get("veo3_model") or "Veo 3.1 - Fast"),
            "create_image_model": str(settings.get("create_image_model") or settings.get("veo3_create_image_model") or "Nano Banana pro"),
            "output_count": 1,
            "video_output_dir": str(reference_output_root),
            "download_images": False,
        }
        override_veo3_output_config(
            reference_project_name,
            reference_output_root,
            aspect_ratio=str(settings.get("veo3_aspect_ratio") or "16:9"),
            output_count=1,
            download_images=False,
        )
        _write_veo3_test_json(root, reference_project_name, reference_project_data, run_mode="Tao anh reference nhan vat")
        reference_workflow = workflow_module.GenerateImageWorkflow(project_name=reference_project_name, project_data=reference_project_data)
        reference_workflow._log = lambda message: _log(log, str(message or ""))  # type: ignore[attr-defined]
        _log(log, f"Bat dau tao anh reference nhan vat VEO/Flow: {len(reference_prompts)} prompt.")
        _run_workflow_until_return_or_complete(
            reference_workflow,
            root=root,
            project_name=reference_project_name,
            expected_prompt_ids=[item["id"] for item in reference_prompt_items],
            expected_output_count=1,
            video_root=reference_output_root,
            log=log,
            media_key="image_paths",
            require_downloads=False,
        )
        _log(log, "Da tao xong batch reference nhan vat. Tiep tuc batch scene cho video.")

    before_images = {str(p.resolve()).lower() for p in collect_image_files(video_root)}

    prompt_items = _build_veo3_prompt_items(prompts)
    project_data = {
        "prompts": {
            "text_to_video": prompt_items
        },
        "_use_project_prompts": True,
        "_worker_controls_lifecycle": False,
        "aspect_ratio": str(settings.get("veo3_aspect_ratio") or "16:9"),
        "veo_model": str(settings.get("veo3_model") or "Veo 3.1 - Fast"),
        "create_image_model": str(settings.get("create_image_model") or settings.get("veo3_create_image_model") or "Nano Banana pro"),
        "output_count": _int_setting(settings, "veo3_output_count", 1, 1, 4),
        "video_output_dir": str(video_root),
        "download_images": True,
    }
    override_veo3_output_config(
        project.name,
        video_root,
        aspect_ratio=str(settings.get("veo3_aspect_ratio") or "16:9"),
        output_count=_int_setting(settings, "veo3_output_count", 1, 1, 4),
        download_images=True,
    )
    _write_veo3_test_json(root, project.name, project_data, run_mode="Tao anh tu text")

    workflow = workflow_module.GenerateImageWorkflow(project_name=project.name, project_data=project_data)
    workflow._log = lambda message: _log(log, str(message or ""))  # type: ignore[attr-defined]
    _log(log, f"Bat dau tao anh VEO/Flow: {len(prompts)} prompt.")
    _run_workflow_until_return_or_complete(
        workflow,
        root=root,
        project_name=project.name,
        expected_prompt_ids=[item["id"] for item in prompt_items],
        expected_output_count=_int_setting(settings, "veo3_output_count", 1, 1, 4),
        video_root=video_root,
        log=log,
        media_key="image_paths",
    )

    after_images = collect_image_files(video_root)
    new_images = [p for p in after_images if str(p.resolve()).lower() not in before_images]
    render_images = sorted(new_images, key=lambda p: p.name.lower())
    if not render_images:
        detail = summarize_veo3_workflow_errors(root, project.name)
        suffix = f"\n\nChi tiet loi VEO3:\n{detail}" if detail else ""
        existing = f"\n\nThu muc hien co {len(after_images)} anh cu, nhung lan chay nay khong tai them anh moi." if after_images else ""
        raise RuntimeError(f"VEO3 chay xong nhung khong thay anh moi tai ve trong: {video_root / 'image'}{existing}{suffix}")
    _log(log, f"VEO3 da tai xong {len(after_images)} anh trong: {video_root / 'image'}")
    return render_images


def run_veo3_generate_shorts_only(project_dir: str | Path, settings: dict, log: Callable[[str], None] | None = None) -> list[Path]:
    project = Path(project_dir)
    root = ensure_veo3_engine(settings)
    log = _project_log_callback(project / "shorts", log)
    _log(log, f"VEO3 engine: {root}")

    prompts, prompt_path, targets = prepare_short_veo_prompt_file(project, limit=8)
    _log(log, f"Da chuan bi {len(prompts)} prompt anh Short: {prompt_path}")

    configure_veo3_for_project(settings, project)
    ensure_veo3_auth_ready(settings, log)

    video_root = project / "shorts" / "veo_videos"
    video_root.mkdir(parents=True, exist_ok=True)
    override_veo3_output_config(project.name + "_shorts", video_root, aspect_ratio="9:16", output_count=1, download_images=True)
    before_images = {str(p.resolve()).lower() for p in collect_image_files(video_root)}

    workflow_module = importlib.import_module("A_workflow_generate_image")
    prompt_items = _build_veo3_prompt_items(prompts)
    project_data = {
        "prompts": {
            "text_to_video": prompt_items
        },
        "_use_project_prompts": True,
        "_worker_controls_lifecycle": False,
        "aspect_ratio": "9:16",
        "veo_model": str(settings.get("veo3_model") or "Veo 3.1 - Fast"),
        "create_image_model": str(settings.get("create_image_model") or settings.get("veo3_create_image_model") or "Nano Banana pro"),
        "output_count": 1,
        "video_output_dir": str(video_root),
        "download_images": True,
    }
    _write_veo3_test_json(root, f"{project.name}_shorts", project_data, run_mode="Tao anh tu text")

    workflow = workflow_module.GenerateImageWorkflow(project_name=f"{project.name}_shorts", project_data=project_data)
    workflow._log = lambda message: _log(log, str(message or ""))  # type: ignore[attr-defined]
    _log(log, f"Bat dau tao anh Shorts 9:16: {len(prompts)} prompt.")
    _run_workflow_until_return_or_complete(
        workflow,
        root=root,
        project_name=f"{project.name}_shorts",
        expected_prompt_ids=[item["id"] for item in prompt_items],
        expected_output_count=1,
        video_root=video_root,
        log=log,
        media_key="image_paths",
    )

    after_images = collect_image_files(video_root)
    new_images = [p for p in after_images if str(p.resolve()).lower() not in before_images]
    render_images = new_images
    if not render_images:
        detail = summarize_veo3_workflow_errors(root, f"{project.name}_shorts")
        suffix = f"\n\nChi tiet loi VEO3 Shorts:\n{detail}" if detail else ""
        existing = f"\n\nThu muc hien co {len(after_images)} anh cu, nhung lan chay nay khong tai them anh moi." if after_images else ""
        raise RuntimeError(f"VEO3 chay xong nhung khong thay anh Short moi tai ve trong: {video_root / 'image'}{existing}{suffix}")
    distribute_short_veo_images(render_images, targets, log=log)
    _log(log, f"VEO3 Shorts da tai xong {len(render_images)} anh trong: {video_root / 'image'}")
    return render_images


def login_veo3_account(settings: dict, log: Callable[[str], None] | None = None) -> dict:
    ensure_veo3_engine(settings)
    configure_veo3_for_project(settings, None)
    login_module = importlib.import_module("login")
    email, password = veo3_credentials(settings)
    if not email or not password:
        raise RuntimeError("Thieu VEO3 email/password trong tab Cau hinh.")
    profile_name = str(settings.get("veo3_profile_name") or "PROFILE_1").strip() or "PROFILE_1"
    _log(log, f"Dang dang nhap VEO3 profile {profile_name}...")
    result = login_module.auto_login_veo3(email, password, profile_name=profile_name, logger=lambda msg: _log(log, msg))
    if not isinstance(result, dict):
        result = {"success": False, "message": "Ket qua dang nhap VEO3 khong hop le."}
    return result


def ensure_veo3_auth_ready(settings: dict, log: Callable[[str], None] | None = None) -> dict:
    auth = veo3_auth_status(settings)
    if auth["ready"]:
        return auth

    missing = ", ".join(auth["missing"])
    _log(log, f"VEO3 thieu token Google Labs ({missing}); tu dong mo Chrome de login/lay token...")
    result = login_veo3_account(settings, log=log)
    if not bool(result.get("success")):
        raise RuntimeError(str(result.get("message") or "VEO3 auto login that bai."))

    auth = veo3_auth_status(settings)
    if auth["ready"]:
        _log(log, "VEO3 da login va lay token xong.")
        return auth

    raise RuntimeError(
        "VEO3 login xong nhung van thieu token Google Labs: "
        f"{', '.join(auth['missing'])}"
    )


def veo3_auth_status(settings: dict) -> dict:
    ensure_veo3_engine(settings)
    sm = importlib.import_module("settings_manager")
    config = sm.SettingsManager.load_config()
    account = config.get("account1") if isinstance(config.get("account1"), dict) else {}
    missing = []
    for key in ("sessionId", "projectId", "access_token"):
        if not str(account.get(key) or "").strip():
            missing.append(key)
    return {"ready": not missing, "missing": missing, "config_path": str(sm.CONFIG_FILE)}


def configure_veo3_for_project(settings: dict, project_dir: Path | None) -> None:
    root = ensure_veo3_engine(settings)
    sm = importlib.import_module("settings_manager")
    config = sm.SettingsManager.load_config()
    if not isinstance(config, dict):
        config = {}

    account = config.get("account1") if isinstance(config.get("account1"), dict) else {}
    account = dict(account or {})
    email, password = veo3_credentials(settings, existing_account=account)
    account_changed = bool(email and str(account.get("email") or "").strip().lower() != email.lower())
    if email:
        account["email"] = email
    if password:
        account["password"] = password
    if account_changed:
        account["sessionId"] = ""
        account["projectId"] = ""
        account["access_token"] = ""
        account["cookie"] = ""
        account["URL_GEN_TOKEN"] = "https://labs.google/fx/vi/tools/flow"
        account["TYPE_ACCOUNT"] = _normalize_account_type(settings.get("veo3_account_type") or "ULTRA")
    account.setdefault("sessionId", "")
    account.setdefault("projectId", "")
    account.setdefault("access_token", "")
    account.setdefault("cookie", "")
    account["TYPE_ACCOUNT"] = _normalize_account_type(settings.get("veo3_account_type") or account.get("TYPE_ACCOUNT") or "ULTRA")
    account.setdefault("URL_GEN_TOKEN", "https://labs.google/fx/vi/tools/flow")
    profile_name = str(settings.get("veo3_profile_name") or "PROFILE_1").strip() or "PROFILE_1"
    account.setdefault("folder_user_data_get_token", str(root / "chrome_user_data" / profile_name))

    config.update(
        {
            "MULTI_VIDEO": _int_setting(settings, "veo3_multi_video", 1, 1, 20),
            "OUTPUT_COUNT": _int_setting(settings, "veo3_output_count", 1, 1, 4),
            "VIDEO_ASPECT_RATIO": str(settings.get("veo3_aspect_ratio") or "16:9"),
            "VEO_MODEL": str(settings.get("veo3_model") or "Veo 3.1 - Fast"),
            "CREATE_IMAGE_MODEL": str(settings.get("create_image_model") or settings.get("veo3_create_image_model") or "Nano Banana pro"),
            "DOWNLOAD_MODE": str(settings.get("veo3_download_mode") or "720"),
            "WAIT_GEN_VIDEO": _int_setting(settings, "veo3_wait_gen_video", 12, 0, 999),
            "RETRY_WITH_ERROR": _int_setting(settings, "veo3_retry_with_error", 5, 0, 99),
            "WAIT_RESEND_VIDEO": _int_setting(settings, "veo3_wait_resend_video", 30, 0, 999),
            "TOKEN_RETRY": _int_setting(settings, "veo3_token_retry", 5, 1, 20),
            "TOKEN_RETRY_DELAY": _int_setting(settings, "veo3_token_retry_delay", 3, 0, 99),
            "CLEAR_DATA": _int_setting(settings, "veo3_clear_data", 5, 0, 999),
            "CLEAR_DATA_WAIT": _int_setting(settings, "veo3_clear_data_wait", 4, 0, 999),
            "DOWNLOAD_IMAGE": _bool_setting(settings, "veo3_download_images", True),
            "account1": account,
        }
    )
    if project_dir is not None:
        out_dir = Path(project_dir) / "veo_videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        config["VIDEO_OUTPUT_DIR"] = str(out_dir)
        config["current_project"] = Path(project_dir).name
    sm.SettingsManager.save_config(config)


def override_veo3_output_config(
    project_name: str,
    output_dir: Path,
    *,
    aspect_ratio: str,
    output_count: int,
    download_images: bool,
) -> None:
    sm = importlib.import_module("settings_manager")
    config = sm.SettingsManager.load_config()
    if not isinstance(config, dict):
        config = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    config.update(
        {
            "VIDEO_OUTPUT_DIR": str(output_dir),
            "VIDEO_ASPECT_RATIO": str(aspect_ratio or "9:16"),
            "OUTPUT_COUNT": int(output_count or 1),
            "DOWNLOAD_IMAGE": bool(download_images),
            "current_project": str(project_name or "shorts"),
        }
    )
    sm.SettingsManager.save_config(config)


def _run_workflow_until_return_or_complete(
    workflow: object,
    *,
    root: Path,
    project_name: str,
    expected_prompt_ids: list[str],
    expected_output_count: int,
    video_root: Path,
    log: Callable[[str], None] | None,
    media_key: str = "image_paths",
    require_downloads: bool = True,
) -> None:
    error: list[BaseException] = []

    def run_workflow() -> None:
        try:
            workflow._run_with_new_loop()  # type: ignore[attr-defined]
        except BaseException as exc:
            error.append(exc)

    worker = threading.Thread(target=run_workflow, name=f"veo3-{project_name}", daemon=True)
    worker.start()

    complete_seen = 0
    while worker.is_alive():
        time.sleep(2)
        if _veo3_state_has_completed_downloads(
            root=root,
            project_name=project_name,
            expected_prompt_ids=expected_prompt_ids,
            expected_output_count=expected_output_count,
            video_root=video_root,
            media_key=media_key,
            require_downloads=require_downloads,
        ):
            complete_seen += 1
            if complete_seen >= 2:
                _log(log, "VEO3 state da du SUCCESSFUL va file tai ve; mo khoa UI du engine chua return.")
                try:
                    setattr(workflow, "STOP", 1)
                except Exception:
                    pass
                return
        else:
            complete_seen = 0

    if error:
        raise error[0]


def _veo3_state_has_completed_downloads(
    *,
    root: Path,
    project_name: str,
    expected_prompt_ids: list[str],
    expected_output_count: int,
    video_root: Path,
    media_key: str = "image_paths",
    require_downloads: bool = True,
) -> bool:
    state_path = Path(root) / "Workflows" / str(project_name) / "state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    prompts = data.get("prompts") if isinstance(data, dict) else {}
    if not isinstance(prompts, dict):
        return False

    expected = max(1, int(expected_output_count or 1))
    for prompt_id in expected_prompt_ids:
        prompt_data = prompts.get(str(prompt_id))
        if not isinstance(prompt_data, dict):
            return False
        statuses = [str(item or "").upper() for item in prompt_data.get("statuses", [])]
        successful_indexes = [index for index, status in enumerate(statuses) if "SUCCESSFUL" in status]
        if len(successful_indexes) < expected:
            return False
        if not require_downloads:
            continue
        media_paths = [str(item or "").strip() for item in prompt_data.get(media_key, [])]
        for index in successful_indexes[:expected]:
            if index >= len(media_paths):
                return False
            path = Path(media_paths[index])
            if not path.is_absolute():
                path = video_root / path
            if not path.exists() or path.stat().st_size <= 0:
                return False
    return True


def ensure_veo3_engine(settings: dict) -> Path:
    raw = str(settings.get("veo3_root") or "veo3-local").strip()
    root = Path(raw)
    if not root.is_absolute():
        root = APP_DIR / root
    root = root.resolve()
    if not (root / "A_workflow_generate_image.py").exists():
        raise FileNotFoundError(f"Khong thay VEO3 engine trong: {root}")
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return root


def veo3_credentials(settings: dict, existing_account: dict | None = None) -> tuple[str, str]:
    existing_account = existing_account or {}
    email = str(settings.get("veo3_email") or existing_account.get("email") or "").strip()
    password = str(settings.get("veo3_password") or existing_account.get("password") or "")
    return email, password


def _int_setting(settings: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(settings.get(key) or default))
        return max(low, min(high, value))
    except Exception:
        return default


def _bool_setting(settings: dict, key: str, default: bool = False) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "bat", "bật", "co", "có"}:
        return True
    if text in {"0", "false", "no", "n", "off", "tat", "tắt", "khong", "không"}:
        return False
    return bool(default)


def _normalize_account_type(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"NORMAL", "PRO", "ULTRA"}:
        return text
    return "ULTRA"


def _build_veo3_prompt_items(prompts: list[str]) -> list[dict]:
    return [
        {"id": f"{idx:03d}", "description": prompt, "prompt": prompt}
        for idx, prompt in enumerate(prompts, start=1)
    ]


def _write_veo3_test_json(root: Path, project_name: str, project_data: dict, run_mode: str = "Tao anh tu text") -> None:
    workflow_dir = root / "Workflows" / project_name
    workflow_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_mode": run_mode,
        "prompts": project_data.get("prompts", {}),
        "aspect_ratio": project_data.get("aspect_ratio"),
        "veo_model": project_data.get("veo_model"),
        "create_image_model": project_data.get("create_image_model"),
        "output_count": project_data.get("output_count"),
        "video_output_dir": project_data.get("video_output_dir"),
        "download_images": bool(project_data.get("download_images", False)),
    }
    (workflow_dir / "test.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def summarize_veo3_workflow_errors(root: Path, project_name: str) -> str:
    workflow_dir = Path(root) / "Workflows" / str(project_name)
    state_path = workflow_dir / "state.json"
    response_paths = [workflow_dir / "response.json", workflow_dir / "respone_anh.json"]
    parts: list[str] = []

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        prompts = state.get("prompts") if isinstance(state, dict) else {}
        if isinstance(prompts, dict):
            for prompt_id, prompt_data in list(prompts.items())[:8]:
                if not isinstance(prompt_data, dict):
                    continue
                errors = [str(x).strip() for x in prompt_data.get("error_messages", []) if str(x or "").strip()]
                codes = [str(x).strip() for x in prompt_data.get("error_codes", []) if str(x or "").strip()]
                statuses = [str(x).strip() for x in prompt_data.get("statuses", []) if str(x or "").strip()]
                if errors or codes:
                    parts.append(
                        f"{prompt_id}: status={','.join(statuses) or '-'} "
                        f"code={','.join(codes) or '-'} msg={'; '.join(errors) or '-'}"
                    )
    except Exception:
        pass

    if not parts:
        for response_path in response_paths:
            try:
                data = json.loads(response_path.read_text(encoding="utf-8"))
                entries = data if isinstance(data, list) else [data]
                for entry in entries[-5:]:
                    if not isinstance(entry, dict):
                        continue
                    prompt_id = entry.get("prompt_id") or "?"
                    response = entry.get("response") if isinstance(entry.get("response"), dict) else entry
                    body = str(response.get("body") or response.get("error") or "")[:500]
                    status = response.get("status") or response.get("reason") or "-"
                    parts.append(f"{prompt_id}: status={status} body={body}")
                if parts:
                    break
            except Exception:
                pass

    return "\n".join(parts[-8:]).strip()


def _project_log_callback(project_dir: Path, callback: Callable[[str], None] | None) -> Callable[[str], None]:
    log_dir = Path(project_dir) / "veo_videos"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_path = log_dir / "veo3_run.log"

    def write(message: str) -> None:
        text = str(message or "")
        if callable(callback):
            callback(text)
        try:
            from datetime import datetime

            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {text}\n")
        except Exception:
            pass

    return write


def _log(callback: Callable[[str], None] | None, message: str) -> None:
    if callable(callback):
        callback(str(message or ""))


def dump_veo3_status(settings: dict) -> str:
    status = veo3_auth_status(settings)
    payload = {
        "ready": status["ready"],
        "missing": status["missing"],
        "config_path": status["config_path"],
    }
    return json.dumps(payload, ensure_ascii=False)
