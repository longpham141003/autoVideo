from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import find_chrome_executable


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


def can_bind_port(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, int(port)))
        return True
    except OSError:
        return False


def is_cdp_ready(host: str, port: int) -> bool:
    try:
        with urlopen(f"http://{host}:{int(port)}/json/version", timeout=1.5) as response:
            return int(response.status or 0) == 200
    except (URLError, OSError, ValueError):
        return False


def wait_for_cdp(host: str, port: int, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + int(timeout_seconds)
    while time.time() < deadline:
        if is_cdp_ready(host, port):
            return True
        time.sleep(0.4)
    return False


def pick_port(host: str, start_port: int, tries: int = 40) -> int:
    base = int(start_port or 9444)
    for port in range(base, base + int(tries)):
        if is_cdp_ready(host, port):
            return port
        if can_bind_port(host, port):
            return port
    raise RuntimeError(f"Khong tim duoc CDP port trong tu {base}.")


def open_cdp_tab(host: str, port: int, url: str) -> bool:
    target = f"http://{host}:{int(port)}/json/new?{quote(url, safe=':/?&=%')}"
    for method in ("PUT", "GET"):
        try:
            request = Request(target, method=method)
            with urlopen(request, timeout=2.5) as response:
                return int(response.status or 0) in {200, 201}
        except Exception:
            continue
    return False


def open_visible_chrome(chrome_exe: str, profile_dir: Path, url: str, host: str, port: int) -> None:
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={int(port)}",
        f"--remote-debugging-address={host}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--new-window",
        "--window-size=1320,900",
        url,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_win_hidden_kwargs())


class ChromeSession:
    def __init__(self, settings: dict):
        self.settings = settings
        self.process: subprocess.Popen | None = None
        self.host = str(settings.get("chrome_cdp_host") or "127.0.0.1")
        self.port = int(settings.get("chrome_cdp_port") or 9444)

    @property
    def cdp_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def profile_dir(self) -> Path:
        root = Path(str(self.settings.get("chrome_profile_root") or "chrome_user_data_chatgpt"))
        name = str(self.settings.get("chrome_profile_name") or "PROFILE_1").strip() or "PROFILE_1"
        return root / name

    def ensure_started(self, log=None) -> str:
        chrome_exe = find_chrome_executable(str(self.settings.get("chrome_exe_path") or ""))
        if not chrome_exe:
            raise FileNotFoundError("Khong tim thay chrome.exe. Hay cai Chrome hoac dien Chrome path trong Settings.")

        profile_dir = self.profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        url = str(self.settings.get("chatgpt_url") or "https://chatgpt.com/")

        if is_cdp_ready(self.host, self.port):
            if callable(log):
                log(f"Đã kết nối Chrome đang mở ở CDP port {self.port}.")
            opened = open_cdp_tab(self.host, self.port, url)
            try:
                open_visible_chrome(chrome_exe, profile_dir, url, self.host, self.port)
            except Exception:
                pass
            if callable(log):
                log("Đã yêu cầu Chrome mở tab ChatGPT." if opened else "CDP đã sẵn sàng, đang mở Chrome visible bằng profile tool.")
            return self.cdp_url

        if not can_bind_port(self.host, self.port):
            self.port = pick_port(self.host, self.port + 1)
            if callable(log):
                log(f"CDP port cũ đang bận, chuyển sang port {self.port}.")

        cmd = [
            chrome_exe,
            f"--remote-debugging-port={self.port}",
            f"--remote-debugging-address={self.host}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-default-apps",
            "--disable-sync",
            "--remote-allow-origins=*",
            "--window-size=1320,900",
            url,
        ]
        if callable(log):
            log(f"Mở Chrome profile: {profile_dir}")
        self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_win_hidden_kwargs())
        if not wait_for_cdp(self.host, self.port, timeout_seconds=35):
            raise RuntimeError(f"Chrome da mo nhung CDP chua san sang o port {self.port}.")
        open_cdp_tab(self.host, self.port, url)
        return self.cdp_url
