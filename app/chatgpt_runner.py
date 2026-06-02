from __future__ import annotations

import asyncio
import time
from typing import Callable

from playwright.async_api import async_playwright

from .chrome_manager import ChromeSession


COMPOSER_JS = r"""
() => {
  const outside = (node) => node && !node.closest("#acvs-root");
  const visible = (node) => {
    if (!node || !outside(node)) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    '[contenteditable="true"][aria-label*="Message"]',
    'textarea[aria-label*="Message"]',
    'main form [contenteditable="true"]',
    'main form textarea',
    'form [contenteditable="true"]',
    'form textarea'
  ];
  for (const selector of selectors) {
    const nodes = [...document.querySelectorAll(selector)].filter(outside);
    const node = nodes.find(visible) || nodes[0];
    if (node) return true;
  }
  return false;
}
"""


FILL_COMPOSER_JS = r"""
(prompt) => {
  const outside = (node) => node && !node.closest("#acvs-root");
  const visible = (node) => {
    if (!node || !outside(node)) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    "#prompt-textarea",
    '[data-testid="prompt-textarea"]',
    '[contenteditable="true"][aria-label*="Message"]',
    'textarea[aria-label*="Message"]',
    'main form [contenteditable="true"]',
    'main form textarea',
    'form [contenteditable="true"]',
    'form textarea'
  ];
  let node = null;
  for (const selector of selectors) {
    const nodes = [...document.querySelectorAll(selector)].filter(outside);
    node = nodes.find(visible) || nodes[0] || null;
    if (node) break;
  }
  if (!node) return false;
  node.scrollIntoView({ block: "center" });
  node.focus();
  if (node.tagName === "TEXTAREA") {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
    if (setter) setter.call(node, prompt);
    else node.value = prompt;
    node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: prompt }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  node.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: prompt }));
  try {
    const selection = document.getSelection();
    selection?.selectAllChildren(node);
    document.execCommand("insertText", false, prompt);
  } catch (_) {
    node.textContent = prompt;
  }
  if (!String(node.innerText || node.textContent || "").includes(String(prompt).slice(0, 24))) {
    node.textContent = prompt;
  }
  node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: prompt }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}
"""


CLICK_SEND_JS = r"""
() => {
  const outside = (node) => node && !node.closest("#acvs-root");
  const visible = (node) => {
    if (!node || !outside(node)) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    'button[data-testid="send-button"]',
    'button[data-testid="composer-submit-button"]',
    'button[aria-label="Send prompt"]',
    'button[aria-label*="Send"]',
    'form button[type="submit"]'
  ];
  for (const selector of selectors) {
    const buttons = [...document.querySelectorAll(selector)].filter(outside);
    const button = buttons.find((candidate) => visible(candidate) && !candidate.disabled) || buttons.find((candidate) => !candidate.disabled);
    if (button) {
      button.click();
      return true;
    }
  }
  return false;
}
"""


ASSISTANT_TEXTS_JS = r"""
() => {
  const roleNodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
  if (roleNodes.length) return roleNodes.map((node) => node.innerText.trim()).filter(Boolean);
  const articles = [...document.querySelectorAll("article")];
  return articles
    .filter((article) => !article.innerText.includes("You said:"))
    .map((article) => article.innerText.trim())
    .filter(Boolean);
}
"""


USER_TEXTS_JS = r"""
() => {
  const roleNodes = [...document.querySelectorAll('[data-message-author-role="user"]')];
  if (roleNodes.length) return roleNodes.map((node) => node.innerText.trim()).filter(Boolean);
  return [];
}
"""


BUSY_JS = r"""
() => {
  const outside = (node) => node && !node.closest("#acvs-root");
  const visible = (node) => {
    if (!node || !outside(node)) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    'button[data-testid="stop-button"]',
    'button[data-testid="composer-submit-button"][aria-label*="Stop"]',
    'button[aria-label*="Stop"]',
    'button[aria-label*="stop"]',
    'button[aria-label*="Cancel"]',
    'button[aria-label*="cancel"]'
  ];
  for (const selector of selectors) {
    if ([...document.querySelectorAll(selector)].filter(outside).some(visible)) return true;
  }
  return [...document.querySelectorAll("button")]
    .filter((button) => outside(button) && visible(button))
    .some((button) => /stop generating|cancel generating/i.test(button.innerText || button.getAttribute("aria-label") || ""));
}
"""


class ChatGPTWebRunner:
    def __init__(self, settings: dict, log: Callable[[str], None] | None = None, stop_check: Callable[[], bool] | None = None):
        self.settings = settings
        self.log = log or (lambda _msg: None)
        self.stop_check = stop_check or (lambda: False)
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.chrome = ChromeSession(settings)
        self._send_lock = asyncio.Lock()

    async def start(self) -> None:
        cdp_url = self.chrome.ensure_started(log=self.log)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
        self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.page = await self._pick_page()
        await self.page.bring_to_front()
        await self._ensure_chatgpt_page()
        self.log("ChatGPT profile da san sang.")

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None

    async def _pick_page(self):
        assert self.context is not None
        for page in self.context.pages:
            if not page.is_closed() and "chatgpt.com" in (page.url or ""):
                return page
        for page in self.context.pages:
            if not page.is_closed():
                return page
        return await self.context.new_page()

    async def _ensure_chatgpt_page(self) -> None:
        assert self.page is not None
        url = self.page.url or ""
        if "chatgpt.com" not in url and "chat.openai.com" not in url:
            await self.page.goto(str(self.settings.get("chatgpt_url") or "https://chatgpt.com/"), wait_until="domcontentloaded", timeout=45000)
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass

    async def wait_for_login_ready(self, timeout_seconds: int = 120) -> None:
        assert self.page is not None
        started = time.time()
        while time.time() - started < timeout_seconds:
            if self.stop_check():
                raise RuntimeError("Stopped.")
            try:
                if await self.page.evaluate(COMPOSER_JS):
                    return
            except Exception:
                pass
            await asyncio.sleep(1.0)
        raise RuntimeError("Chua thay o nhap ChatGPT. Hay dang nhap trong Chrome profile vua mo, sau do bam Run lai.")

    async def send_prompt(self, prompt: str, label: str = "step", timeout_seconds: int = 1200, image_path: str = "") -> str:
        async with self._send_lock:
            return await self._send_prompt_locked(prompt, label=label, timeout_seconds=timeout_seconds, image_path=image_path)

    async def _send_prompt_locked(self, prompt: str, label: str = "step", timeout_seconds: int = 1200, image_path: str = "") -> str:
        if self.page is None:
            raise RuntimeError("ChatGPT runner chua start.")
        await self.wait_for_login_ready(timeout_seconds=120)
        await self._wait_until_not_busy(timeout_seconds=300)
        before = await self._assistant_texts()
        before_count = len(before)
        before_last = before[-1] if before else ""
        before_user_count = len(await self._user_texts())
        self.log(f"Gui prompt: {label} ({len(prompt)} ky tu)")
        if image_path:
            await self._attach_file(image_path)
        ok = await self.page.evaluate(FILL_COMPOSER_JS, prompt)
        if not ok:
            raise RuntimeError("Khong dien duoc prompt vao ChatGPT.")
        await asyncio.sleep(0.6)

        clicked = False
        for _ in range(60):
            if self.stop_check():
                raise RuntimeError("Stopped.")
            try:
                clicked = bool(await self.page.evaluate(CLICK_SEND_JS))
            except Exception:
                clicked = False
            if clicked:
                break
            await asyncio.sleep(0.5)
        if not clicked:
            raise RuntimeError("Khong bam duoc nut Send cua ChatGPT.")
        await self._wait_for_user_submit(before_user_count, prompt, timeout_seconds=45)
        return await self._wait_for_answer(before_count, before_last, timeout_seconds=timeout_seconds)

    async def _attach_file(self, image_path: str) -> None:
        if self.page is None:
            return
        import os

        if not image_path or not os.path.exists(image_path):
            raise FileNotFoundError(f"Khong thay file anh thumb: {image_path}")
        try:
            inputs = await self.page.query_selector_all('input[type="file"]')
            if inputs:
                await inputs[-1].set_input_files(image_path)
                self.log(f"Da dinh kem anh: {os.path.basename(image_path)}")
                await asyncio.sleep(2.5)
                return
        except Exception as exc:
            self.log(f"Dinh kem anh qua input co san loi: {exc}")

        for selector in [
            'button[aria-label*="Attach"]',
            'button[aria-label*="Upload"]',
            'button[data-testid*="attachment"]',
            'button:has-text("Attach")',
        ]:
            try:
                async with self.page.expect_file_chooser(timeout=3500) as chooser_info:
                    await self.page.locator(selector).first.click(timeout=2500)
                chooser = await chooser_info.value
                await chooser.set_files(image_path)
                self.log(f"Da dinh kem anh: {os.path.basename(image_path)}")
                await asyncio.sleep(2.5)
                return
            except Exception:
                continue
        raise RuntimeError("Khong tim thay nut/input dinh kem anh tren ChatGPT.")

    async def _assistant_texts(self) -> list[str]:
        if self.page is None:
            return []
        try:
            texts = await self.page.evaluate(ASSISTANT_TEXTS_JS)
            if isinstance(texts, list):
                return [str(item).strip() for item in texts if str(item).strip()]
        except Exception:
            return []
        return []

    async def _user_texts(self) -> list[str]:
        if self.page is None:
            return []
        try:
            texts = await self.page.evaluate(USER_TEXTS_JS)
            if isinstance(texts, list):
                return [str(item).strip() for item in texts if str(item).strip()]
        except Exception:
            return []
        return []

    async def _is_busy(self) -> bool:
        if self.page is None:
            return False
        try:
            return bool(await self.page.evaluate(BUSY_JS))
        except Exception:
            return False

    async def _wait_until_not_busy(self, timeout_seconds: int = 300) -> None:
        started = time.time()
        while time.time() - started < timeout_seconds:
            if self.stop_check():
                raise RuntimeError("Stopped.")
            if not await self._is_busy():
                return
            await asyncio.sleep(1.0)
        raise RuntimeError("ChatGPT van dang tra loi prompt truoc, khong gui prompt moi de tranh bi lap.")

    async def _wait_for_user_submit(self, before_user_count: int, prompt: str, timeout_seconds: int = 45) -> None:
        started = time.time()
        prefix = " ".join(str(prompt or "").split())[:80]
        while time.time() - started < timeout_seconds:
            if self.stop_check():
                raise RuntimeError("Stopped.")
            users = await self._user_texts()
            if len(users) > before_user_count:
                last = " ".join(users[-1].split())
                if not prefix or prefix[:40] in last:
                    return
                return
            if await self._is_busy():
                return
            await asyncio.sleep(0.5)
        self.log("Canh bao: chua xac nhan duoc ChatGPT da nhan prompt, tiep tuc doi cau tra loi.")

    async def _wait_for_answer(self, before_count: int, before_last: str, timeout_seconds: int) -> str:
        started = time.time()
        last_text = before_last or ""
        last_count = before_count
        last_change = time.time()
        last_log = 0.0
        while time.time() - started < timeout_seconds:
            if self.stop_check():
                raise RuntimeError("Stopped.")
            texts = await self._assistant_texts()
            current = texts[-1] if texts else ""
            if len(texts) != last_count or current != last_text:
                last_count = len(texts)
                last_text = current
                last_change = time.time()
            answer_started = len(texts) > before_count or current != before_last
            stable = time.time() - last_change
            busy = await self._is_busy()
            if answer_started and current.strip() and not busy and stable >= 8.0:
                return current.strip()
            if answer_started and current.strip() and stable >= 90.0:
                self.log("ChatGPT co ve da dung cap nhat nhung UI van bao busy; lay cau tra loi hien co de tiep tuc.")
                return current.strip()
            if time.time() - last_log >= 30:
                self.log(f"Dang doi ChatGPT tra loi... stable={int(stable)}s busy={busy}")
                last_log = time.time()
            await asyncio.sleep(1.0)
        raise RuntimeError("Timeout khi doi ChatGPT tra loi.")
