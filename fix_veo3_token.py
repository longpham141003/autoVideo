"""
fix_veo3_token.py — Debug tool tim selector moi cua VEO3 Flow UI.
Chay: python fix_veo3_token.py

Mo Chrome, navigate den labs.google/fx/vi/tools/flow,
va in ra tat ca selector co the dung de cap nhat A_workflow_get_token.py.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "veo3-local"))

FLOW_URL = "https://labs.google/fx/vi/tools/flow"

SELECTORS_TO_TRY = [
    # Text inputs
    "textarea",
    'input[type="text"]',
    '[contenteditable="true"]',
    '[role="textbox"]',
    # Class patterns
    '[class*="prompt"]',
    '[class*="input"]',
    '[class*="textarea"]',
    '[class*="editor"]',
    # Placeholder patterns
    '[placeholder*="Describe"]',
    '[placeholder*="Mo ta"]',
    '[placeholder*="describe"]',
    # Buttons
    'button[class*="generate"]',
    'button[class*="create"]',
    'button[class*="submit"]',
    '[role="button"]',
    # General
    "[data-testid]",
    "[aria-label]",
    # VEO specific patterns
    '[class*="flow"]',
    '[class*="veo"]',
    '[class*="media"]',
    '[class*="generate"]',
]


async def scan_selectors():
    try:
        from chrome import ensure_playwright, launch_persistent_chrome
    except ImportError as e:
        print(f"ERROR: Cannot import chrome module: {e}")
        print("Make sure you are running from the autoVideo root directory.")
        return

    pw = ensure_playwright()
    user_data_dir = str(Path("veo3-local/chrome_user_data/PROFILE_1").resolve())
    print(f"[INFO] Chrome user data: {user_data_dir}")

    browser = await launch_persistent_chrome(pw, headless=False, user_data_dir=user_data_dir)
    page = browser.pages[0] if browser.pages else await browser.new_page()

    print(f"[INFO] Navigating to {FLOW_URL}...")
    await page.goto(FLOW_URL, wait_until="networkidle", timeout=60000)

    print("\n" + "=" * 60)
    print("PLEASE LOG IN TO VEO3 MANUALLY")
    print("After login, navigate to the Flow/Tools page")
    print("Then press ENTER in this terminal to scan selectors...")
    print("=" * 60 + "\n")

    try:
        input()
    except EOFError:
        print("[INFO] Non-interactive mode, scanning in 5s...")
        await asyncio.sleep(5)

    print("\n[SCAN] Found elements:\n")
    found_any = False

    for sel in SELECTORS_TO_TRY:
        try:
            elements = await page.query_selector_all(sel)
            if not elements:
                continue
            found_any = True
            for el in elements[:3]:
                text = await el.inner_text()
                text = text.strip()[:100] if text else "(empty)"
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                cls = await el.evaluate("el => el.className") or ""
                placeholder = await el.evaluate("el => el.getAttribute('placeholder')") or ""
                testid = await el.evaluate("el => el.getAttribute('data-testid')") or ""
                aria = await el.evaluate("el => el.getAttribute('aria-label')") or ""

                extra = ""
                if placeholder:
                    extra += f" placeholder='{placeholder[:50]}'"
                if testid:
                    extra += f" data-testid='{testid[:50]}'"
                if aria:
                    extra += f" aria-label='{aria[:50]}'"

                print(f"  {sel}: <{tag} class='{str(cls)[:80]}'{extra}>")
                print(f"          text: {text}")
                print()
        except Exception:
            pass

    if not found_any:
        print("  (No matching elements found. The VEO UI may have changed significantly.)")
        print("  Try inspecting the page manually (F12) and look for:")
        print("  - The text input for prompts")
        print("  - The generate/submit button")
        print("  - Any iframe or shadow DOM elements")

    print("\n[STRUCTURE] Page body overview:")
    try:
        body_html = await page.evaluate("() => document.body.innerHTML.substring(0, 2000)")
        print(body_html[:1500])
    except Exception as e:
        print(f"  Error getting body: {e}")

    print("\n[DONE] Use the selectors above to update A_workflow_get_token.py")
    print("        Focus on textarea/input elements and buttons.")
    await browser.close()


if __name__ == "__main__":
    asyncio.run(scan_selectors())
