from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import APP_DIR
from .shorts_builder import collect_image_files, distribute_short_veo_images, prepare_short_veo_prompt_file
from .veo_prompts import prepare_character_reference_prompt_file, prepare_veo_prompt_file
from .video_editor import render_project_video

try:
    from core import secrets as _secrets
except ImportError:  # core/ chua co hoac chay standalone
    _secrets = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Dong bo hoa
#
# Engine VEO3 doc cau hinh tu MOT file global (data_general/config.json), nen
# hai batch chay song song se dap len nhau. Truoc day chinh la nguyen nhan
# batch "reference nhan vat" ghi de cau hinh cua batch "scene":
#   configure_veo3_for_project()  -> ghi config
#   override_veo3_output_config() -> ghi de lai cung file ngay sau do
#
# _VEO3_CONFIG_LOCK: bao ve chu ky doc-sua-ghi cua config.json.
# _VEO3_BATCH_LOCK : serialize ca mot batch (ghi config + chay workflow), vi
#                    engine doc config trong suot qua trinh chay.
# ---------------------------------------------------------------------------
_VEO3_CONFIG_LOCK = threading.RLock()
_VEO3_BATCH_LOCK = threading.RLock()

DEFAULT_ASPECT_RATIO = "16:9"
SHORTS_ASPECT_RATIO = "9:16"
DEFAULT_VEO_MODEL = "Veo 3.1 - Fast"
DEFAULT_IMAGE_MODEL = "Nano Banana pro"
FLOW_TOKEN_URL = "https://labs.google/fx/vi/tools/flow"


@dataclass
class ImageBatch:
    """Mo ta mot lan chay tao anh qua engine VEO3/Flow."""

    project_name: str
    prompts: list[str]
    output_root: Path
    aspect_ratio: str
    output_count: int
    download_images: bool
    label: str
    run_mode: str = "Tao anh tu text"
    require_downloads: bool = True
    veo_model: str = DEFAULT_VEO_MODEL
    image_model: str = DEFAULT_IMAGE_MODEL
    prompt_items: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
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

    aspect_ratio = _aspect_ratio(settings)
    veo_model = _veo_model(settings)
    image_model = _image_model(settings)
    scene_output_count = _int_setting(settings, "veo3_output_count", 1, 1, 4)

    reference_prompts, reference_prompt_path = prepare_character_reference_prompt_file(
        project,
        limit=_int_setting(settings, "veo_character_reference_prompt_limit", 64, 1, 200),
    )
    prompts, prompt_path = prepare_veo_prompt_file(
        project,
        limit=_int_setting(settings, "veo_prompt_limit", 160, 1, 500),
        character_consistency=_bool_setting(settings, "veo_character_consistency", True),
    )
    if reference_prompts and reference_prompt_path is not None:
        _log(log, f"Da chuan bi {len(reference_prompts)} prompt reference nhan vat: {reference_prompt_path}")
    _log(log, f"Da chuan bi {len(prompts)} prompt tao anh: {prompt_path}")

    ensure_veo3_auth_ready(settings, log)

    _log(
        log,
        "Luu y: neu Chrome token hien 'Da co loi xay ra, vui long thu lai' "
        "sau khi tu dien prompt thi do la request UI bi chan de lay token; hay xem log API ben duoi de biet loi that.",
    )

    # Batch 1 (tuy chon): anh reference nhan vat.
    if reference_prompts:
        _run_image_batch(
            ImageBatch(
                project_name=f"{project.name}_characters",
                prompts=reference_prompts,
                output_root=project / "character_references",
                aspect_ratio=aspect_ratio,
                output_count=1,
                download_images=False,
                label="reference nhan vat",
                run_mode="Tao anh reference nhan vat",
                require_downloads=False,
                veo_model=veo_model,
                image_model=image_model,
            ),
            settings=settings,
            root=root,
            project_dir=project,
            log=log,
        )
        _log(log, "Da tao xong batch reference nhan vat. Tiep tuc batch scene cho video.")

    # Batch 2: anh scene dung de dung video.
    return _run_image_batch(
        ImageBatch(
            project_name=project.name,
            prompts=prompts,
            output_root=project / "veo_videos",
            aspect_ratio=aspect_ratio,
            output_count=scene_output_count,
            download_images=True,
            label="scene",
            veo_model=veo_model,
            image_model=image_model,
        ),
        settings=settings,
        root=root,
        project_dir=project,
        log=log,
        sort_results=True,
    )


def run_veo3_generate_shorts_only(project_dir: str | Path, settings: dict, log: Callable[[str], None] | None = None) -> list[Path]:
    project = Path(project_dir)
    root = ensure_veo3_engine(settings)
    log = _project_log_callback(project / "shorts", log)
    _log(log, f"VEO3 engine: {root}")

    prompts, prompt_path, targets = prepare_short_veo_prompt_file(project, limit=8)
    _log(log, f"Da chuan bi {len(prompts)} prompt anh Short: {prompt_path}")

    ensure_veo3_auth_ready(settings, log)

    render_images = _run_image_batch(
        ImageBatch(
            project_name=f"{project.name}_shorts",
            prompts=prompts,
            output_root=project / "shorts" / "veo_videos",
            aspect_ratio=SHORTS_ASPECT_RATIO,
            output_count=1,
            download_images=True,
            label="Shorts 9:16",
            veo_model=_veo_model(settings),
            image_model=_image_model(settings),
        ),
        settings=settings,
        root=root,
        project_dir=project,
        log=log,
    )
    distribute_short_veo_images(render_images, targets, log=log)
    return render_images


# ---------------------------------------------------------------------------
# Batch runner dung chung
# ---------------------------------------------------------------------------
def _run_image_batch(
    batch: ImageBatch,
    *,
    settings: dict,
    root: Path,
    project_dir: Path,
    log: Callable[[str], None] | None,
    sort_results: bool = False,
) -> list[Path]:
    """Chay mot batch tao anh. Gop logic cua ca luong scene, reference, Shorts.

    Giu _VEO3_BATCH_LOCK trong suot batch vi engine doc cau hinh global
    trong luc chay; hai batch chay chong nhau se doc nham cau hinh cua nhau.
    """
    if not batch.prompts:
        raise RuntimeError(f"Khong co prompt nao de tao anh {batch.label}.")

    with _VEO3_BATCH_LOCK:
        batch.output_root.mkdir(parents=True, exist_ok=True)
        batch.prompt_items = _build_veo3_prompt_items(batch.prompts)

        project_data = {
            "prompts": {"text_to_video": batch.prompt_items},
            "_use_project_prompts": True,
            "_worker_controls_lifecycle": False,
            "aspect_ratio": batch.aspect_ratio,
            "veo_model": batch.veo_model,
            "create_image_model": batch.image_model,
            "output_count": batch.output_count,
            "video_output_dir": str(batch.output_root),
            "download_images": batch.download_images,
        }

        # MOT lan ghi config duy nhat cho ca batch, gom luon phan output
        # override. Truoc day day la hai lan ghi tach roi va lan sau xoa
        # ket qua cua lan truoc.
        configure_veo3_for_project(
            settings,
            project_dir,
            output_dir=batch.output_root,
            current_project=batch.project_name,
            aspect_ratio=batch.aspect_ratio,
            output_count=batch.output_count,
            download_images=batch.download_images,
        )
        _write_veo3_test_json(root, batch.project_name, project_data, run_mode=batch.run_mode)

        before_images = {str(p.resolve()).lower() for p in collect_image_files(batch.output_root)}

        workflow_module = importlib.import_module("A_workflow_generate_image")
        workflow = workflow_module.GenerateImageWorkflow(
            project_name=batch.project_name,
            project_data=project_data,
        )
        workflow._log = lambda message: _log(log, str(message or ""))  # type: ignore[attr-defined]

        _log(log, f"Bat dau tao anh {batch.label}: {len(batch.prompts)} prompt.")
        _run_workflow_until_return_or_complete(
            workflow,
            root=root,
            project_name=batch.project_name,
            expected_prompt_ids=[item["id"] for item in batch.prompt_items],
            expected_output_count=batch.output_count,
            video_root=batch.output_root,
            log=log,
            require_downloads=batch.require_downloads,
        )

        after_images = collect_image_files(batch.output_root)
        new_images = [p for p in after_images if str(p.resolve()).lower() not in before_images]
        if sort_results:
            new_images = sorted(new_images, key=lambda p: p.name.lower())

        if not new_images:
            raise RuntimeError(_no_new_images_message(root, batch, after_images))

        _log(log, f"VEO3 ({batch.label}) da tai xong {len(new_images)} anh moi trong: {batch.output_root / 'image'}")
        return new_images


def _no_new_images_message(root: Path, batch: ImageBatch, after_images: list[Path]) -> str:
    detail = summarize_veo3_workflow_errors(root, batch.project_name)
    suffix = f"\n\nChi tiet loi VEO3 ({batch.label}):\n{detail}" if detail else ""
    existing = (
        f"\n\nThu muc hien co {len(after_images)} anh cu, nhung lan chay nay khong tai them anh moi."
        if after_images
        else ""
    )
    return (
        f"VEO3 chay xong nhung khong thay anh {batch.label} moi tai ve trong: "
        f"{batch.output_root / 'image'}{existing}{suffix}"
    )


# ---------------------------------------------------------------------------
# Dang nhap / token
# ---------------------------------------------------------------------------
def login_veo3_account(settings: dict, log: Callable[[str], None] | None = None) -> dict:
    ensure_veo3_engine(settings)
    configure_veo3_for_project(settings, None)
    login_module = importlib.import_module("login")
    email, password = veo3_credentials(settings)
    if not email or not password:
        raise RuntimeError("Thieu VEO3 email/password trong tab Cau hinh.")
    profile_name = _profile_name(settings)
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
    with _VEO3_CONFIG_LOCK:
        config = sm.SettingsManager.load_config()
    account = config.get("account1") if isinstance(config.get("account1"), dict) else {}
    missing = [key for key in ("sessionId", "projectId", "access_token") if not str(account.get(key) or "").strip()]
    return {"ready": not missing, "missing": missing, "config_path": str(sm.CONFIG_FILE)}


# ---------------------------------------------------------------------------
# Cau hinh engine
# ---------------------------------------------------------------------------
def configure_veo3_for_project(
    settings: dict,
    project_dir: Path | None,
    *,
    output_dir: Path | None = None,
    current_project: str | None = None,
    aspect_ratio: str | None = None,
    output_count: int | None = None,
    download_images: bool | None = None,
) -> None:
    """Ghi toan bo cau hinh engine VEO3 trong MOT thao tac duy nhat.

    Cac tham so keyword la phan override rieng cho tung batch. Truoc day
    chung nam o `override_veo3_output_config()` va duoc goi thanh mot lan
    ghi rieng ngay sau ham nay -- tao ra khoang thoi gian config o trang
    thai sai, va khien batch nay dap len batch kia.
    """
    root = ensure_veo3_engine(settings)
    sm = importlib.import_module("settings_manager")

    with _VEO3_CONFIG_LOCK:
        config = sm.SettingsManager.load_config()
        if not isinstance(config, dict):
            config = {}

        account = dict(config.get("account1") or {}) if isinstance(config.get("account1"), dict) else {}
        email, password = veo3_credentials(settings, existing_account=account)
        account_changed = bool(email and str(account.get("email") or "").strip().lower() != email.lower())
        if email:
            account["email"] = email
        if password:
            # Engine doc truc tiep account["password"] nen o day van phai la
            # plaintext. Diem khac biet: settings.json cua app khong con bat
            # buoc chua plaintext nua (xem veo3_credentials + core.secrets),
            # va file config.json nay da nam trong .gitignore.
            account["password"] = password
        if account_changed:
            account.update({
                "sessionId": "",
                "projectId": "",
                "access_token": "",
                "cookie": "",
                "URL_GEN_TOKEN": FLOW_TOKEN_URL,
            })
        for key in ("sessionId", "projectId", "access_token", "cookie"):
            account.setdefault(key, "")
        account["TYPE_ACCOUNT"] = _normalize_account_type(
            settings.get("veo3_account_type") or account.get("TYPE_ACCOUNT") or "ULTRA"
        )
        account.setdefault("URL_GEN_TOKEN", FLOW_TOKEN_URL)
        account.setdefault(
            "folder_user_data_get_token",
            str(root / "chrome_user_data" / _profile_name(settings)),
        )

        config.update({
            # Luu y: config.yaml khai bao multi_video: 3, con day mac dinh 1.
            # Gia tri trong settings.json cua app moi la nguon quyet dinh.
            "MULTI_VIDEO": _int_setting(settings, "veo3_multi_video", 1, 1, 20),
            "OUTPUT_COUNT": _int_setting(settings, "veo3_output_count", 1, 1, 4),
            "VIDEO_ASPECT_RATIO": _aspect_ratio(settings),
            "VEO_MODEL": _veo_model(settings),
            "CREATE_IMAGE_MODEL": _image_model(settings),
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
        })

        if project_dir is not None:
            default_out = Path(project_dir) / "veo_videos"
            default_out.mkdir(parents=True, exist_ok=True)
            config["VIDEO_OUTPUT_DIR"] = str(default_out)
            config["current_project"] = Path(project_dir).name

        # Override rieng cho batch -- ap dung trong cung lan ghi.
        if output_dir is not None:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            config["VIDEO_OUTPUT_DIR"] = str(output_dir)
        if aspect_ratio is not None:
            config["VIDEO_ASPECT_RATIO"] = str(aspect_ratio)
        if output_count is not None:
            config["OUTPUT_COUNT"] = max(1, int(output_count))
        if download_images is not None:
            config["DOWNLOAD_IMAGE"] = bool(download_images)
        if current_project is not None:
            config["current_project"] = str(current_project)

        sm.SettingsManager.save_config(config)


def override_veo3_output_config(
    project_name: str,
    output_dir: Path,
    *,
    aspect_ratio: str,
    output_count: int,
    download_images: bool,
) -> None:
    """Giu lai cho code cu. Nen dung configure_veo3_for_project(...) thay the.

    Ham nay chi con sua dung phan output, va co lock bao ve.
    """
    sm = importlib.import_module("settings_manager")
    with _VEO3_CONFIG_LOCK:
        config = sm.SettingsManager.load_config()
        if not isinstance(config, dict):
            config = {}
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        config.update({
            "VIDEO_OUTPUT_DIR": str(output_dir),
            "VIDEO_ASPECT_RATIO": str(aspect_ratio or SHORTS_ASPECT_RATIO),
            "OUTPUT_COUNT": max(1, int(output_count or 1)),
            "DOWNLOAD_IMAGE": bool(download_images),
            "current_project": str(project_name or "shorts"),
        })
        sm.SettingsManager.save_config(config)


# ---------------------------------------------------------------------------
# Vong doi workflow
# ---------------------------------------------------------------------------
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
    join_timeout: float = 30.0,
) -> None:
    error: list[BaseException] = []

    def run_workflow() -> None:
        try:
            workflow._run_with_new_loop()  # type: ignore[attr-defined]
        except BaseException as exc:  # noqa: BLE001 - can bat het de bao lai cho luong chinh
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
            log=log,
        ):
            complete_seen += 1
            if complete_seen >= 2:
                _log(log, "VEO3 state da du SUCCESSFUL va file tai ve; yeu cau engine dung lai.")
                _request_workflow_stop(workflow, log)
                # Truoc day ham return ngay tai day trong khi thread van con
                # song va con dang ghi file -> race voi buoc quet anh moi.
                # Gio doi thread ket thuc han (co gioi han thoi gian).
                worker.join(timeout=join_timeout)
                if worker.is_alive():
                    _log(
                        log,
                        f"Engine VEO3 chua thoat sau {join_timeout:.0f}s ke tu khi yeu cau dung. "
                        "Tiep tuc voi so anh da tai ve duoc.",
                    )
                break
        else:
            complete_seen = 0

    if error:
        raise error[0]


def _request_workflow_stop(workflow: object, log: Callable[[str], None] | None) -> None:
    try:
        setattr(workflow, "STOP", 1)
    except (AttributeError, TypeError) as exc:
        # Truoc day la `except Exception: pass` -- loi bien mat khong dau vet.
        _log(log, f"Canh bao: khong dat duoc co STOP cho workflow VEO3 ({exc}).")


def _veo3_state_has_completed_downloads(
    *,
    root: Path,
    project_name: str,
    expected_prompt_ids: list[str],
    expected_output_count: int,
    video_root: Path,
    media_key: str = "image_paths",
    require_downloads: bool = True,
    log: Callable[[str], None] | None = None,
) -> bool:
    state_path = Path(root) / "Workflows" / str(project_name) / "state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Binh thuong o dau batch: engine chua kip tao state.json.
        return False
    except (OSError, json.JSONDecodeError):
        # Cung binh thuong: engine dang ghi do file. Khong log de tranh spam
        # vi ham nay chay 2 giay mot lan.
        return False

    prompts = data.get("prompts") if isinstance(data, dict) else None
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
            try:
                if not path.is_file() or path.stat().st_size <= 0:
                    return False
            except OSError as exc:
                _log(log, f"Canh bao: khong doc duoc file anh {path} ({exc}).")
                return False
    return True


# ---------------------------------------------------------------------------
# Tien ich
# ---------------------------------------------------------------------------
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
    """Lay email/password VEO3.

    Mat khau co the duoc luu duoi dang da ma hoa (`veo3_password_enc`, hoac
    `veo3_password` co tien to "enc:v1:"). Plaintext cu van doc duoc binh
    thuong nen khong pha config hien tai.
    """
    existing_account = existing_account or {}
    email = str(settings.get("veo3_email") or existing_account.get("email") or "").strip()

    raw_password = (
        settings.get("veo3_password_enc")
        or settings.get("veo3_password")
        or existing_account.get("password")
        or ""
    )
    return email, _decrypt_password(raw_password)


def _decrypt_password(raw: object) -> str:
    text = str(raw or "")
    if not text or _secrets is None or not _secrets.is_encrypted(text):
        return text
    try:
        return _secrets.decrypt_secret(text)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Khong giai ma duoc mat khau VEO3: {exc}. "
            "Hay nhap lai mat khau trong tab Cau hinh."
        ) from exc


def encrypt_veo3_password(plain: str) -> str:
    """Dung cho tab Cau hinh: ma hoa mat khau truoc khi luu vao settings.json.

    Luu ket qua vao key `veo3_password_enc`.
    """
    if _secrets is None:
        raise RuntimeError("Thieu module core.secrets; khong ma hoa duoc mat khau.")
    return _secrets.encrypt_secret(plain)


def _aspect_ratio(settings: dict) -> str:
    return str(settings.get("veo3_aspect_ratio") or DEFAULT_ASPECT_RATIO)


def _veo_model(settings: dict) -> str:
    return str(settings.get("veo3_model") or DEFAULT_VEO_MODEL)


def _image_model(settings: dict) -> str:
    return str(
        settings.get("create_image_model")
        or settings.get("veo3_create_image_model")
        or DEFAULT_IMAGE_MODEL
    )


def _profile_name(settings: dict) -> str:
    return str(settings.get("veo3_profile_name") or "PROFILE_1").strip() or "PROFILE_1"


def _int_setting(settings: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(settings.get(key) or default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _bool_setting(settings: dict, key: str, default: bool = False) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "bat", "b\u1eadt", "co", "c\u00f3"}:
        return True
    if text in {"0", "false", "no", "n", "off", "tat", "t\u1eaft", "khong", "kh\u00f4ng"}:
        return False
    return bool(default)


def _normalize_account_type(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in {"NORMAL", "PRO", "ULTRA"} else "ULTRA"


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
    """Doc state.json / response.json de lay ly do that su VEO3 that bai.

    Truoc day cac loi doc file bi nuot bang `except Exception: pass`, nen khi
    khong tom duoc nguyen nhan thi cung khong biet la vi khong co loi hay vi
    doc file hong. Gio bao cao ro tung truong hop.
    """
    workflow_dir = Path(root) / "Workflows" / str(project_name)
    state_path = workflow_dir / "state.json"
    response_paths = [workflow_dir / "response.json", workflow_dir / "respone_anh.json"]
    parts: list[str] = []
    read_problems: list[str] = []

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
    except FileNotFoundError:
        read_problems.append(f"Khong co {state_path.name} (engine chua chay den buoc ghi state).")
    except (OSError, json.JSONDecodeError) as exc:
        read_problems.append(f"Khong doc duoc {state_path.name}: {exc}")

    if not parts:
        for response_path in response_paths:
            try:
                data = json.loads(response_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                read_problems.append(f"Khong doc duoc {response_path.name}: {exc}")
                continue

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

    if not parts and read_problems:
        return "\n".join(read_problems)
    return "\n".join(parts[-8:]).strip()


def _project_log_callback(project_dir: Path, callback: Callable[[str], None] | None) -> Callable[[str], None]:
    from datetime import datetime

    log_dir = Path(project_dir) / "veo_videos"
    log_path = log_dir / "veo3_run.log"
    file_logging_enabled = True
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        file_logging_enabled = False
        print(f"[veo3] Khong tao duoc thu muc log {log_dir}: {exc}", file=sys.stderr)

    warned: list[bool] = []

    def write(message: str) -> None:
        text = str(message or "")
        if callable(callback):
            callback(text)
        if not file_logging_enabled:
            return
        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {text}\n")
        except OSError as exc:
            # Chi canh bao mot lan, tranh spam neu o dia day.
            if not warned:
                warned.append(True)
                print(f"[veo3] Khong ghi duoc log {log_path}: {exc}", file=sys.stderr)

    return write


def _log(callback: Callable[[str], None] | None, message: str) -> None:
    if callable(callback):
        callback(str(message or ""))


def dump_veo3_status(settings: dict) -> str:
    status = veo3_auth_status(settings)
    return json.dumps(
        {
            "ready": status["ready"],
            "missing": status["missing"],
            "config_path": status["config_path"],
        },
        ensure_ascii=False,
    )
