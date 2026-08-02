"""
core/manifest.py — Stage-based state management with fail-resume (ported from review-phim).
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

MANIFEST_VERSION = "2.0"

STAGE_ORDER = [
    "s0_prepare", "s1_script", "s2_voice",
    "s3_prompts", "s4_images", "s5_video", "s6_shorts",
]

STAGE_LABELS = {
    "s0_prepare": "Chuan bi", "s1_script": "Kich ban",
    "s2_voice": "Giong doc", "s3_prompts": "Prompt anh",
    "s4_images": "Tao anh", "s5_video": "Ghep video",
    "s6_shorts": "Shorts",
}


class ManifestError(Exception):
    pass


def create_manifest(job_dir: Path, project_name: str, *, input_source="",
                    input_type="transcript", metadata=None) -> dict:
    now = datetime.now().isoformat()
    m = {
        "manifest_version": MANIFEST_VERSION,
        "project_name": project_name,
        "job_dir": str(job_dir.resolve()),
        "created_at": now, "updated_at": now,
        "input": {"source": input_source, "type": input_type},
        "metadata": metadata or {},
        "stages": {}, "current_stage": None,
        "errors": [], "artifacts": {},
    }
    for sid in STAGE_ORDER:
        m["stages"][sid] = {
            "status": "pending", "started_at": None, "finished_at": None,
            "duration_seconds": None, "error": None, "outputs": {},
        }
    save_manifest(job_dir, m)
    return m


def load_manifest(job_dir: Path) -> dict:
    p = Path(job_dir) / "manifest.json"
    if not p.exists():
        raise ManifestError(f"Manifest not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"Invalid JSON: {e}")


def save_manifest(job_dir: Path, manifest: dict) -> bool:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    mp = job_dir / "manifest.json"
    tp = job_dir / "manifest.json.tmp"
    manifest["updated_at"] = datetime.now().isoformat()
    try:
        tp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tp, mp)
        return True
    except Exception:
        tp.unlink(missing_ok=True)
        return False


def stage_start(job_dir: Path, sid: str) -> dict:
    m = load_manifest(job_dir)
    if sid not in m["stages"]:
        raise ManifestError(f"Invalid stage: {sid}")
    m["current_stage"] = sid
    m["stages"][sid]["status"] = "running"
    m["stages"][sid]["started_at"] = datetime.now().isoformat()
    m["stages"][sid]["error"] = None
    save_manifest(job_dir, m)
    return m


def stage_done(job_dir: Path, sid: str, *, outputs=None, artifacts=None) -> dict:
    m = load_manifest(job_dir)
    s = m["stages"][sid]
    s["status"] = "done"
    s["finished_at"] = datetime.now().isoformat()
    if s["started_at"]:
        d = datetime.fromisoformat(s["finished_at"]) - datetime.fromisoformat(s["started_at"])
        s["duration_seconds"] = round(d.total_seconds(), 1)
    if outputs:
        s["outputs"] = outputs
    if artifacts:
        m["artifacts"].update(artifacts)
    save_manifest(job_dir, m)
    return m


def stage_fail(job_dir: Path, sid: str, error: str) -> dict:
    m = load_manifest(job_dir)
    s = m["stages"][sid]
    s["status"] = "failed"
    s["finished_at"] = datetime.now().isoformat()
    s["error"] = error
    if s["started_at"]:
        d = datetime.fromisoformat(s["finished_at"]) - datetime.fromisoformat(s["started_at"])
        s["duration_seconds"] = round(d.total_seconds(), 1)
    m["errors"].append({
        "stage": sid, "error": error,
        "timestamp": datetime.now().isoformat(),
    })
    save_manifest(job_dir, m)
    return m


def stage_skip(job_dir: Path, sid: str, reason="") -> dict:
    m = load_manifest(job_dir)
    m["stages"][sid]["status"] = "skipped"
    m["stages"][sid]["error"] = reason or "Skipped"
    save_manifest(job_dir, m)
    return m


def get_next_pending_stage(job_dir: Path) -> str | None:
    m = load_manifest(job_dir)
    for sid in STAGE_ORDER:
        if m["stages"][sid]["status"] in ("pending", "failed"):
            return sid
    return None


def is_stage_done(job_dir: Path, sid: str) -> bool:
    return load_manifest(job_dir)["stages"].get(sid, {}).get("status") == "done"


def is_job_done(job_dir: Path) -> bool:
    m = load_manifest(job_dir)
    return all(m["stages"][s]["status"] in ("done", "skipped") for s in STAGE_ORDER)


def get_job_summary(job_dir: Path) -> str:
    m = load_manifest(job_dir)
    icons = {"pending": "...", "running": ">>", "done": "OK", "failed": "XX", "skipped": "--"}
    lines = [f"Job: {m['project_name']}"]
    for sid in STAGE_ORDER:
        s = m["stages"][sid]
        icon = icons.get(s["status"], "??")
        label = STAGE_LABELS.get(sid, sid)
        dur = f" ({s['duration_seconds']}s)" if s.get("duration_seconds") else ""
        err = f" - {s['error'][:80]}" if s.get("error") else ""
        lines.append(f"  [{icon}] {label}: {s['status']}{dur}{err}")
    return "\n".join(lines)


def get_failed_stages(job_dir: Path) -> list[str]:
    m = load_manifest(job_dir)
    return [s for s in STAGE_ORDER if m["stages"][s]["status"] == "failed"]
