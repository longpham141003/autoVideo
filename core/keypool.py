"""
core/keypool.py — API key rotation pool (ported from review-phim).
Round-robin + exponential cooldown on 429 rate limits.
"""
import itertools
import threading
import time
from typing import Any, Callable


class KeyPoolError(Exception):
    pass


class KeyPool:
    def __init__(self, keys: list[str], *, cooldown_s=60, max_cooldown_s=3600, multiplier=2.0):
        if not keys:
            raise ValueError("Need at least 1 key")
        self._keys = list(keys)
        self._cooldown_base = cooldown_s
        self._cooldown_max = max_cooldown_s
        self._multiplier = multiplier
        self._cooldown_until: dict[str, float] = {}
        self._consecutive_429: dict[str, int] = {}
        self._requests: dict[str, int] = {}
        self._rate_limits: dict[str, int] = {}
        self._iter = itertools.cycle(range(len(self._keys)))
        self._lock = threading.Lock()

    def acquire(self, timeout=0) -> str:
        deadline = time.time() + timeout if timeout > 0 else 0
        with self._lock:
            while True:
                for _ in range(len(self._keys)):
                    key = self._keys[next(self._iter)]
                    if time.time() >= self._cooldown_until.get(key, 0):
                        return key
                if timeout <= 0 or time.time() >= deadline:
                    raise KeyPoolError("No available keys")
                time.sleep(0.5)

    def report_success(self, key: str):
        with self._lock:
            self._requests[key] = self._requests.get(key, 0) + 1
            self._consecutive_429[key] = 0

    def report_429(self, key: str):
        with self._lock:
            self._requests[key] = self._requests.get(key, 0) + 1
            self._rate_limits[key] = self._rate_limits.get(key, 0) + 1
            self._consecutive_429[key] = self._consecutive_429.get(key, 0) + 1
            n = self._consecutive_429[key]
            self._cooldown_until[key] = time.time() + min(
                self._cooldown_base * (self._multiplier ** (n - 1)),
                self._cooldown_max)

    def report_error(self, key: str, code=0):
        with self._lock:
            self._requests[key] = self._requests.get(key, 0) + 1
            if code == 403:
                self._cooldown_until[key] = time.time() + 30

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_keys": len(self._keys),
                "requests": dict(self._requests),
                "rate_limits": dict(self._rate_limits),
                "cooling_down": [k for k, t in self._cooldown_until.items() if time.time() < t],
            }

    def reset(self):
        with self._lock:
            self._cooldown_until.clear()
            self._consecutive_429.clear()


def retry_with_pool(pool: KeyPool, fn: Callable, *, max_retries=5) -> Any:
    key = ""
    for _ in range(max_retries):
        try:
            key = pool.acquire(timeout=30)
            r = fn(key)
            pool.report_success(key)
            return r
        except KeyPoolError:
            raise
        except Exception as e:
            s = str(e).lower()
            if any(w in s for w in ("429", "rate limit", "resource_exhausted")):
                pool.report_429(key)
            elif "403" in s:
                pool.report_error(key, 403)
            else:
                raise
    raise KeyPoolError("Max retries exceeded")


def create_gemini_pool(keys):
    return KeyPool(keys, cooldown_s=90, max_cooldown_s=7200, multiplier=1.5)


def create_veo3_pool(keys):
    return KeyPool(keys, cooldown_s=30, max_cooldown_s=600, multiplier=1.3)
