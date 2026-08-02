"""
providers/image_gen.py — Unified image generation provider.
Port pattern tu review-phim: tach provider khoi workflow, dung keypool + retry.

Thay the GenerateImageWorkflow cu bang API truc tiep + browser fallback.
"""
import asyncio
import json
import random
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

IMAGE_ASPECT_RATIO_LANDSCAPE = "IMAGE_ASPECT_RATIO_LANDSCAPE"
IMAGE_ASPECT_RATIO_PORTRAIT = "IMAGE_ASPECT_RATIO_PORTRAIT"

MODEL_MAP = {
    "Nano Banana pro": "GEM_PIX_2",
    "Nano Banana 2": "NARWHAL",
    "Nano Banana": "GEM_PIX",
    "Imagen 4": "IMAGEN_3_5",
}

API_BASE = "https://aisandbox-pa.googleapis.com/v1/projects/"
API_SUFFIX = "/flowMedia:batchGenerateImages"


class ImageGenError(Exception):
    pass


class ImageGenProvider:
    """
    Provider tao anh qua VEO3/Flow API.

    Usage:
        provider = ImageGenProvider(session_id, project_id, access_token, cookie)
        result = await provider.generate("a cat in a coffee shop")
    """

    def __init__(self, session_id: str, project_id: str, access_token: str,
                 cookie: str = "", *, model: str = "Nano Banana pro",
                 output_dir: Path | str | None = None,
                 log_callback: Callable | None = None):
        self.session_id = session_id
        self.project_id = project_id
        self.access_token = access_token
        self.cookie = cookie
        self.model = model
        self.model_key = MODEL_MAP.get(model, "IMAGEN_3_5")
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
        self.output_dir = output_dir or Path("output/images")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log = log_callback or print

        self._api_url = API_BASE + str(project_id) + API_SUFFIX
        self._page = None  # Playwright page for browser fallback

    def set_browser_page(self, page):
        """Gan Playwright page de dung browser fallback khi API direct fail."""
        self._page = page

    async def generate(self, prompt: str, *,
                       aspect_ratio: str = "16:9",
                       output_count: int = 1,
                       seed: int | None = None,
                       retry_count: int = 3,
                       timeout: int = 80) -> dict[str, Any]:
        """
        Tao anh tu prompt.

        Returns:
            {"success": True, "image_paths": [...], "image_urls": [...],
             "prompt_id": str, "count": int}
        """
        aspect = IMAGE_ASPECT_RATIO_PORTRAIT if "9:16" in aspect_ratio else IMAGE_ASPECT_RATIO_LANDSCAPE
        effective_seed = seed if seed is not None else random.randint(0, 294967295)
        prompt_id = str(uuid.uuid4())[:8]

        payload = self._build_payload(prompt, aspect, output_count, effective_seed)

        last_error = None
        for attempt in range(retry_count):
            try:
                self.log(f"[ImageGen] Generating (attempt {attempt + 1}/{retry_count}): {prompt[:100]}...")

                # Try API direct first
                response = await self._call_api_direct(payload, timeout)
                if response.get("ok"):
                    return self._process_response(response, prompt_id)

                error_code = str(response.get("status") or "")
                error_body = response.get("body", "")

                # 403 -> try browser fallback (better cookies)
                if "403" in error_code or "403" in error_body:
                    self.log(f"[ImageGen] 403 - trying browser fallback (attempt {attempt + 1})")
                    if self._page:
                        response = await self._call_via_browser(payload, timeout * 1000)
                        if response.get("ok"):
                            return self._process_response(response, prompt_id)

                last_error = response.get("error") or response.get("reason") or f"HTTP {error_code}"

            except asyncio.TimeoutError:
                last_error = f"Timeout after {timeout}s"
                self.log(f"[ImageGen] TIMEOUT: {last_error}")

            except Exception as e:
                last_error = str(e)
                self.log(f"[ImageGen] ERROR: {e}")

            if attempt < retry_count - 1:
                wait = 15 * (attempt + 1)
                self.log(f"[ImageGen] Retrying in {wait}s...")
                await asyncio.sleep(wait)

        return {"success": False, "error": last_error, "prompt_id": prompt_id}

    def _build_payload(self, prompt, aspect, count, seed) -> dict:
        client_ctx = {
            "recaptchaContext": {"token": "", "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB"},
            "sessionId": self.session_id,
            "projectId": self.project_id,
            "tool": "PINHOLE",
        }
        requests_list = []
        for i in range(count):
            requests_list.append({
                "clientContext": dict(client_ctx),
                "imageAspectRatio": aspect,
                "seed": (seed + i) % 294967296,
                "imageModelName": self.model_key,
                "prompt": prompt,
                "imageInputs": [],
            })
        return {"clientContext": client_ctx, "requests": requests_list}

    async def _call_api_direct(self, payload, timeout) -> dict:
        """Goi API truc tiep bang urllib (chay trong thread de khong block loop)."""
        def _do_request():
            data = json.dumps(payload).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            }
            if self.cookie:
                headers["Cookie"] = self.cookie
            req = urllib.request.Request(self._api_url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8")}
            except urllib.error.HTTPError as e:
                return {"ok": False, "status": e.code, "body": e.read().decode("utf-8", errors="replace")}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return await asyncio.to_thread(_do_request)

    async def _call_via_browser(self, payload, timeout_ms=30000) -> dict:
        """Fallback: goi qua Playwright browser page (dung cookie that cua trinh duyet)."""
        if not self._page:
            return {"ok": False, "error": "No browser page available"}
        try:
            response = await self._page.request.post(
                self._api_url,
                data=json.dumps(payload),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.access_token}",
                },
                timeout=int(timeout_ms),
            )
            return {"ok": response.ok, "status": response.status, "body": await response.text()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _process_response(self, response, prompt_id) -> dict:
        """Parse response, download images."""
        body = response.get("body", "")
        try:
            data = json.loads(body)
        except Exception:
            return {"success": False, "error": "Invalid JSON response", "prompt_id": prompt_id}

        media = self._extract_media(data)
        if not media:
            return {"success": False, "error": "No images in response", "prompt_id": prompt_id}

        image_paths = []
        image_urls = []
        for i, m in enumerate(media):
            url = m.get("downloadUrl") or m.get("uri") or ""
            if not url:
                continue
            image_urls.append(url)
            path = self._download_image(url, f"{prompt_id}_{i + 1}")
            if path:
                image_paths.append(str(path))

        return {
            "success": True,
            "prompt_id": prompt_id,
            "image_paths": image_paths,
            "image_urls": image_urls,
            "count": len(image_paths),
        }

    def _extract_media(self, data: Any) -> list[dict]:
        """Duyet de quy response tim moi URL anh - khong phu thuoc schema co dinh."""
        results = []

        def _walk(obj):
            if isinstance(obj, dict):
                url = obj.get("downloadUrl") or obj.get("uri") or obj.get("fifeUrl")
                if url:
                    results.append({"downloadUrl": url, "mimeType": obj.get("mimeType")})
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(data)
        return results

    def _download_image(self, url: str, name: str) -> Path | None:
        if not url.startswith("http"):
            return None
        ts = datetime.now().strftime("%d%m%Y_%H%M%S")
        path = self.output_dir / f"{name}_{ts}.jpg"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                while True:
                    chunk = r.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
            return path
        except Exception as e:
            self.log(f"[ImageGen] Download failed: {e}")
            return None


class BatchImageRunner:
    """Chay batch tao anh song song co gioi han - thay the GenerateImageWorkflow cu."""

    def __init__(self, provider: ImageGenProvider, prompts: list[dict], *,
                 wait_between: int = 15, max_concurrent: int = 3,
                 on_progress: Callable | None = None):
        self.provider = provider
        self.prompts = prompts
        self.wait_between = wait_between
        self.max_concurrent = max_concurrent
        self.on_progress = on_progress
        self.results: list[dict] = []
        self.errors: list[dict] = []

    async def run(self) -> dict:
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_one(prompt_data: dict) -> dict:
            async with semaphore:
                pid = prompt_data.get("id", "?")
                text = prompt_data.get("description") or prompt_data.get("prompt", "")
                if self.on_progress:
                    self.on_progress(pid, "generating", text[:100])

                result = await self.provider.generate(text)
                result["_prompt_id"] = pid
                result["_prompt_text"] = text[:200]

                if not result.get("success"):
                    self.errors.append(result)
                else:
                    self.results.append(result)

                if self.on_progress:
                    status = "done" if result.get("success") else "failed"
                    self.on_progress(pid, status, result.get("error", ""))
                return result

        tasks = []
        for p in self.prompts:
            tasks.append(asyncio.create_task(process_one(p)))
            await asyncio.sleep(self.wait_between)

        await asyncio.gather(*tasks, return_exceptions=True)

        return {
            "total": len(self.prompts),
            "success": len(self.results),
            "failed": len(self.errors),
            "results": self.results,
            "errors": self.errors,
        }
