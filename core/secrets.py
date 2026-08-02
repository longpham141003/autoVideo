"""Ma hoa cac gia tri nhay cam luu duoi local (mat khau VEO3, ...).

Boi canh: day la tool chay tren may ca nhan, khong co key server. Nen muc
tieu thuc te la *khong luu mat khau dang plaintext trong file config*, chu
khong phai chong duoc nguoi da chiem quyen dieu khien may.

Cach lam:
- Sinh mot key ngau nhien luu o `<app>/.secret.key` (chmod 600, da gitignore).
- Neu co thu vien `cryptography` thi dung Fernet (AES-128-CBC + HMAC).
- Neu khong co thi fallback sang XOR + HMAC keyed bang chinh key do. Fallback
  chi la obfuscation, KHONG phai ma hoa manh -- ham `backend_name()` cho biet
  dang chay che do nao de UI canh bao nguoi dung.

Gia tri sau khi ma hoa luon co tien to "enc:v1:" nen `decrypt_secret()` phan
biet duoc voi mat khau plaintext cu va tu dong doc duoc ca hai.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets as _secrets
import sys
from pathlib import Path

PREFIX = "enc:v1:"
KEY_FILENAME = ".secret.key"

_APP_DIR = Path(__file__).resolve().parents[1]


def _key_path() -> Path:
    override = os.getenv("AUTOVIDEO_SECRET_KEY_PATH", "").strip()
    if override:
        return Path(override)
    return _APP_DIR / KEY_FILENAME


def _load_or_create_key() -> bytes:
    path = _key_path()
    try:
        if path.is_file():
            raw = path.read_bytes().strip()
            if raw:
                return raw
    except OSError as exc:
        print(f"[secrets] Khong doc duoc key file {path}: {exc}", file=sys.stderr)

    key = base64.urlsafe_b64encode(_secrets.token_bytes(32))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:
        print(f"[secrets] Khong ghi duoc key file {path}: {exc}", file=sys.stderr)
    return key


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    try:
        return Fernet(_derive_fernet_key(_load_or_create_key()))
    except Exception as exc:
        print(f"[secrets] Khong khoi tao duoc Fernet: {exc}", file=sys.stderr)
        return None


def _derive_fernet_key(raw_key: bytes) -> bytes:
    digest = hashlib.sha256(raw_key).digest()
    return base64.urlsafe_b64encode(digest)


def backend_name() -> str:
    """Tra ve "fernet" hoac "xor-fallback" de UI biet muc do bao ve."""
    return "fernet" if _fernet() is not None else "xor-fallback"


def _xor_encrypt(plain: str, raw_key: bytes) -> str:
    nonce = _secrets.token_bytes(16)
    stream_key = hashlib.sha256(raw_key + nonce).digest()
    data = plain.encode("utf-8")
    stream = b""
    counter = 0
    while len(stream) < len(data):
        stream += hashlib.sha256(stream_key + counter.to_bytes(4, "big")).digest()
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(raw_key, nonce + cipher, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")


def _xor_decrypt(token: str, raw_key: bytes) -> str:
    blob = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, tag, cipher = blob[:16], blob[16:32], blob[32:]
    expected = hmac.new(raw_key, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Secret bi sua doi hoac key khong khop.")
    stream_key = hashlib.sha256(raw_key + nonce).digest()
    stream = b""
    counter = 0
    while len(stream) < len(cipher):
        stream += hashlib.sha256(stream_key + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_secret(plain: str) -> str:
    """Ma hoa mot chuoi. Chuoi rong tra ve chuoi rong (khong ma hoa)."""
    text = str(plain or "")
    if not text:
        return ""
    if is_encrypted(text):
        return text

    fernet = _fernet()
    if fernet is not None:
        return PREFIX + "f:" + fernet.encrypt(text.encode("utf-8")).decode("ascii")
    return PREFIX + "x:" + _xor_encrypt(text, _load_or_create_key())


def decrypt_secret(value: object) -> str:
    """Giai ma. Neu gia tri la plaintext cu thi tra ve nguyen ven.

    Nho vay code goi khong can biet mat khau da duoc ma hoa hay chua, va
    config cu van chay binh thuong.
    """
    text = str(value or "")
    if not text or not is_encrypted(text):
        return text

    body = text[len(PREFIX):]
    try:
        if body.startswith("f:"):
            fernet = _fernet()
            if fernet is None:
                raise RuntimeError(
                    "Mat khau duoc ma hoa bang Fernet nhung thieu thu vien 'cryptography'. "
                    "Chay: pip install cryptography"
                )
            return fernet.decrypt(body[2:].encode("ascii")).decode("utf-8")
        if body.startswith("x:"):
            return _xor_decrypt(body[2:], _load_or_create_key())
    except Exception as exc:
        raise RuntimeError(f"Khong giai ma duoc secret: {exc}") from exc

    raise RuntimeError("Dinh dang secret khong nhan dang duoc.")


def mask(value: object, keep: int = 2) -> str:
    """Che gia tri de in ra log an toan."""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep:
        return "*" * len(text)
    return text[:keep] + "*" * (len(text) - keep)
