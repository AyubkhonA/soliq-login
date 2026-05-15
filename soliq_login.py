import asyncio
import json
import re
import sys
import websockets
import pyautogui
import time
from playwright.async_api import async_playwright

EIMZO_WS = "ws://127.0.0.1:64646/service/cryptapi"
INN = "312035036"

async def eimzo(ws, plugin, name, arguments=None):
    payload = {"plugin": plugin, "name": name}
    if arguments:
        payload["arguments"] = arguments
    await ws.send(json.dumps(payload))
    data = json.loads(await ws.recv())
    sys.stdout.buffer.write(f"[E-IMZO] {plugin}.{name} -> status={data.get('status')}\n".encode("utf-8"))
    return data

async def find_and_load_key():
    async with websockets.connect(
        EIMZO_WS,
        additional_headers={"Origin": "https://my.soliq.uz"}
    ) as ws:
        resp = await eimzo(ws, "pfx", "list_all_certificates")
        certs = resp.get("certificates", [])

        target = next(
            (c for c in certs
             if c.get("name", "").upper().find(f"DS{INN}") >= 0
             or f"1.2.860.3.16.1.1={INN}" in c.get("alias", "")),
            None
        )
        if not target:
            raise Exception(f"No cert for INN {INN}")

        cert_name = target["name"]
        cert_alias = target["alias"]
        disk = "D:\\"

        sys.stdout.buffer.write(f"Found: {cert_name}\n".encode("utf-8"))

        m = re.search(r"[-. _]+([A-Za-z0-9]+)\s*$", cert_name)
        pin = m.group(1) if m else ""
        sys.stdout.buffer.write(f"PIN: {pin}\n".encode("utf-8"))

        load = await eimzo(ws, "pfx", "load_key", [disk, "DSKEYS", cert_name, cert_alias])
        if load.get("status", -1) <= 0:
            raise Exception(f"load_key failed: {load}")

        key_id = load.get("keyId")
        sys.stdout.buffer.write(f"keyId: {key_id}\n".encode("utf-8"))
        return key_id, pin, cert_name, cert_alias, disk

def handle_eimzo_pin_dialog(pin, timeout=10):
    """Find and fill E-IMZO Java PIN dialog."""
    import pygetwindow as gw

    PIN_KEYWORDS = ["imzo", "pin", "пароль", "парол", "password", "калит", "kalit", "введите"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        wins = gw.getAllWindows()
        # Log all windows first pass for debug
        titles = [w.title for w in wins if w.title.strip()]
        sys.stdout.buffer.write(("Windows: " + str(titles[:10]) + "\n").encode("utf-8", errors="replace"))

        for w in wins:
            t = w.title.lower()
            if any(kw in t for kw in PIN_KEYWORDS):
                sys.stdout.buffer.write(f"PIN dialog found: {w.title}\n".encode("utf-8", errors="replace"))
                try:
                    w.activate()
                except:
                    pass
                time.sleep(0.5)
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)
                pyautogui.typewrite(pin, interval=0.05)
                pyautogui.press("enter")
                return True

        # Also check if any NEW small window appeared (Java dialogs often have no title)
        small_wins = [w for w in wins if w.title == "" and w.width < 600 and w.height < 400]
        if small_wins:
            w = small_wins[0]
            sys.stdout.buffer.write(f"Untitled small window found: {w.width}x{w.height}\n".encode("utf-8"))
            try:
                w.activate()
            except:
                pass
            time.sleep(0.5)
            pyautogui.typewrite(pin, interval=0.05)
            pyautogui.press("enter")
            return True

        time.sleep(0.5)
    return False

async def login():
    sys.stdout.buffer.write(b"Connecting to E-IMZO...\n")
    key_id, pin, cert_name, cert_alias, disk = await find_and_load_key()

    sys.stdout.buffer.write(b"Opening browser...\n")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

        # Log network requests for debugging
        async def on_request(req):
            if "auth" in req.url or "login" in req.url or "sign" in req.url or "token" in req.url:
                sys.stdout.buffer.write(f"[REQ] {req.method} {req.url[:100]}\n".encode("utf-8"))
        page.on("request", on_request)

        await page.goto("https://my.soliq.uz/main/")
        await page.wait_for_load_state("networkidle")

        # Step 1: Юридик шахслар учун
        await page.get_by_text("Юридик шахслар учун").first.click()
        await page.wait_for_load_state("networkidle")

        # Step 2: Кабинетга кириш
        await page.get_by_text("Кабинетга кириш").first.click()
        await page.wait_for_load_state("networkidle")

        # Step 3: ESI орқали
        await page.get_by_text("ESI орқали").first.click()
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

        # Step 4: Open dropdown then select cert
        # First open the dropdown (click the dropdown toggle)
        dd_toggle = page.locator("[class*='chosen'], [class*='select2'], .dropdown, select, [data-toggle]").first
        if await dd_toggle.count() > 0:
            await dd_toggle.click()
            sys.stdout.buffer.write(b"Opened dropdown\n")
            await asyncio.sleep(1)

        # Now find visible option containing INN
        selected = False
        all_li = page.locator("li, [role='option']")
        count = await all_li.count()
        sys.stdout.buffer.write(f"Options total: {count}\n".encode("utf-8"))
        for i in range(count):
            el = all_li.nth(i)
            try:
                visible = await el.is_visible()
                if not visible:
                    continue
                txt = await el.inner_text()
                if INN in txt or cert_name[:10] in txt:
                    await el.click()
                    sys.stdout.buffer.write(f"Selected: {txt[:80]}\n".encode("utf-8"))
                    selected = True
                    break
            except:
                continue

        if not selected:
            sys.stdout.buffer.write(b"WARNING: cert not selected from dropdown\n")

        await asyncio.sleep(1)

        # Fill password/PIN field if visible on page after cert selection
        pwd = page.locator("input[type='password'], input[name*='pass'], input[id*='pass'], input[placeholder*='arol']")
        pwd_count = await pwd.count()
        sys.stdout.buffer.write(f"Password fields found: {pwd_count}\n".encode("utf-8"))
        for i in range(pwd_count):
            if await pwd.nth(i).is_visible():
                await pwd.nth(i).fill(pin)
                sys.stdout.buffer.write(f"Password filled: {pin}\n".encode("utf-8"))

        await page.screenshot(path="C:/Users/khanm/step_before_kirish.png")

        # Check for captcha before clicking Kirish
        await asyncio.sleep(1)
        captcha_img = page.locator("img[src*='captcha'], img[alt*='captcha'], img[alt*='CAPTCHA']")
        if await captcha_img.count() > 0:
            await page.screenshot(path="C:/Users/khanm/captcha_screen.png")
            sys.stdout.buffer.write(b"CAPTCHA detected! Screenshot: C:/Users/khanm/captcha_screen.png\n")
            sys.stdout.buffer.write(b"Please check captcha_screen.png and enter code manually\n")
            # Wait for user to fill captcha manually (30s)
            await asyncio.sleep(30)

        # Step 5: Click Kirish
        kirish_btn = page.get_by_role("button", name="Kirish")
        if await kirish_btn.count() > 0:
            await kirish_btn.first.click()
            sys.stdout.buffer.write(b"Kirish clicked\n")
        else:
            # Try by text
            await page.get_by_text("Kirish").first.click()
            sys.stdout.buffer.write(b"Kirish (by text) clicked\n")

        # Handle potential E-IMZO PIN dialog (runs in thread)
        loop = asyncio.get_event_loop()
        dialog_handled = await loop.run_in_executor(None, handle_eimzo_pin_dialog, pin, 8)
        sys.stdout.buffer.write(f"PIN dialog handled: {dialog_handled}\n".encode("utf-8"))

        # Wait for navigation
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass
        await asyncio.sleep(3)

        # After ESI auth, navigate to main page to complete login
        sys.stdout.buffer.write(b"Navigating to main page...\n")
        await page.goto("https://my.soliq.uz/main/")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        await page.screenshot(path="C:/Users/khanm/soliq_after_login.png")
        current_url = page.url
        sys.stdout.buffer.write(f"Current URL: {current_url}\n".encode("utf-8"))
        sys.stdout.buffer.write(b"After-login screenshot saved\n")
        sys.stdout.buffer.write(b"Browser stays open 120s - check window\n")

        await asyncio.sleep(120)
        await browser.close()

asyncio.run(login())
