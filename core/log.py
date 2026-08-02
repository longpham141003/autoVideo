"""
core/log.py — Structured logging (ported from review-phim).
Console + file + UI callback support.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable


class ProjectLogger:
    def __init__(self, name: str, log_file: Path | None = None):
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._callbacks: list[Callable[[str], None]] = []
        if not any(isinstance(h, logging.StreamHandler) for h in self._logger.handlers):
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(message)s", "%H:%M:%S"))
            self._logger.addHandler(ch)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            self._logger.addHandler(fh)

    def add_callback(self, cb: Callable[[str], None]):
        self._callbacks.append(cb)

    def _emit(self, msg: str):
        for cb in self._callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    def info(self, msg, *args):
        self._logger.info(msg, *args)
        self._emit(msg % args if args else msg)

    def warning(self, msg, *args):
        self._logger.warning(msg, *args)
        self._emit(f"WARN: {msg % args if args else msg}")

    def error(self, msg, *args):
        self._logger.error(msg, *args)
        self._emit(f"ERROR: {msg % args if args else msg}")

    def success(self, msg, *args):
        self._logger.info(msg, *args)
        self._emit(f"OK: {msg % args if args else msg}")

    def step(self, stage, msg, *args):
        text = f"[{stage}] {msg % args if args else msg}"
        self._logger.info(text)
        self._emit(text)


_loggers: dict[str, ProjectLogger] = {}


def get_logger(name="autoVideo", log_file=None) -> ProjectLogger:
    if isinstance(log_file, str):
        log_file = Path(log_file)
    key = f"{name}:{log_file}" if log_file else name
    if key not in _loggers:
        _loggers[key] = ProjectLogger(name, log_file)
    return _loggers[key]


def get_project_logger(project_dir: Path, project_name="") -> ProjectLogger:
    log_dir = Path(project_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return get_logger(
        f"project:{project_name or Path(project_dir).name}",
        log_dir / f"run_{ts}.log")


def info(msg, *args):
    for logger in _loggers.values():
        logger.info(msg, *args)


def warning(msg, *args):
    for logger in _loggers.values():
        logger.warning(msg, *args)


def error(msg, *args):
    for logger in _loggers.values():
        logger.error(msg, *args)
