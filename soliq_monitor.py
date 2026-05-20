"""
soliq_monitor.py
Run with: python soliq_monitor.py
Command phrase: "check my soliq" or "analyze my firm"

Flow:
1. Login to monitoring web app -> scrape company list (name + INN)
2. For each company:
   a. Login to soliq.uz via EDS cert
   b. Navigate to tax reports -> scrape accepted reports
   c. Update monitoring web app cells
3. Print summary table
"""

import asyncio
import json
import re
import sys
import time
import datetime
import websockets
import pyautogui
from playwright.async_api import async_playwright

# ─── Config ───────────────────────────────────────────────────────────────────
EIMZO_WS = "ws://127.0.0.1:64646/service/cryptapi"
MONITORING_URL = "https://khan-accounting-static-app.netlify.app/monitoring"
MONITORING_EMAIL = "worker1@test.com"
MONITORING_PASSWORD = "Test12345"

# Пакет № prefix -> monitoring column
REPORT_TO_COL = {
    "11101": "НДФЛ 15",
    "10006": "НДС 20",
    "11104": "ПРИБЫЛЬ 20",
    "10205": "АВАНСОВЫЙ",
}

# td index within each company row (0 = company cell)
COL_INDEX = {
    "НДФЛ 15":     1,
    "НДФЛ ОПЛАТА": 2,
    "НДС 20":      3,
    "НДС ОПЛАТА":  4,
    "ПРИБЫЛЬ 20":  5,
    "АВАНСОВЫЙ":   6,
}

MONTH_UZ = {
    1: "Январь", 2: "Феврал", 3: "Март", 4: "Апрел",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

# User input → webapp display month → soliq.uz Давр (tax period = prev month)
MONTH_MAP = {
    "january":   {"variants": ["january", "jan", "1", "01", "январь", "янв", "январ"],
                  "webapp": "Январь",   "period": "Декабрь",   "period_num": 12, "prev_year": True},
    "february":  {"variants": ["february", "feb", "2", "02", "феврал", "февраль", "февр"],
                  "webapp": "Феврал",   "period": "Январь",    "period_num": 1,  "prev_year": False},
    "march":     {"variants": ["march", "mar", "3", "03", "март", "мар"],
                  "webapp": "Март",     "period": "Феврал",    "period_num": 2,  "prev_year": False},
    "april":     {"variants": ["april", "apr", "4", "04", "апрел", "апрель", "апр"],
                  "webapp": "Апрел",    "period": "Март",      "period_num": 3,  "prev_year": False},
    "may":       {"variants": ["may", "5", "05", "май"],
                  "webapp": "Май",      "period": "Апрел",     "period_num": 4,  "prev_year": False},
    "june":      {"variants": ["june", "jun", "6", "06", "июнь", "июн"],
                  "webapp": "Июнь",     "period": "Май",       "period_num": 5,  "prev_year": False},
    "july":      {"variants": ["july", "jul", "7", "07", "июль", "июл"],
                  "webapp": "Июль",     "period": "Июнь",      "period_num": 6,  "prev_year": False},
    "august":    {"variants": ["august", "aug", "8", "08", "август", "авг"],
                  "webapp": "Август",   "period": "Июль",      "period_num": 7,  "prev_year": False},
    "september": {"variants": ["september", "sep", "9", "09", "сентябрь", "сент"],
                  "webapp": "Сентябрь", "period": "Август",    "period_num": 8,  "prev_year": False},
    "october":   {"variants": ["october", "oct", "10", "октябрь", "окт"],
                  "webapp": "Октябрь",  "period": "Сентябрь",  "period_num": 9,  "prev_year": False},
    "november":  {"variants": ["november", "nov", "11", "ноябрь", "ноя"],
                  "webapp": "Ноябрь",   "period": "Октябрь",   "period_num": 10, "prev_year": False},
    "december":  {"variants": ["december", "dec", "12", "декабрь", "дек"],
                  "webapp": "Декабрь",  "period": "Ноябрь",    "period_num": 11, "prev_year": False},
}


def parse_month_input(user_input: str):
    """Match user input against all month variants. Returns month key or None."""
    normalized = user_input.strip().lower()
    for month_key, info in MONTH_MAP.items():
        if normalized in info["variants"]:
            return month_key
    return None


def build_month_info(month_key: str) -> dict:
    """Build full month_info dict from a MONTH_MAP key, including year calculation."""
    info = MONTH_MAP[month_key]
    current_year = datetime.datetime.now().year
    webapp_year = current_year
    # January special case: tax period is December of the PREVIOUS year
    tax_period_year = current_year - 1 if info["prev_year"] else current_year
    return {
        "month_key":      month_key,
        "webapp_month":   info["webapp"],
        "webapp_year":    webapp_year,
        "tax_period":     info["period"],
        "tax_period_year": tax_period_year,
        "period_num":     info["period_num"],
        "prev_year":      info["prev_year"],
    }


def get_month_from_user() -> dict:
    """
    Prompt user for month. Returns month_info dict.
    Empty input = current month default.
    Warns on future month selection.
    """
    current_month = datetime.datetime.now().month
    current_year  = datetime.datetime.now().year

    print("\n" + "=" * 60)
    print("  MONTH SELECTION")
    print("=" * 60)
    print("  Examples: May  /  май  /  5  /  05  /  Апрел")
    print("  Press Enter to use current month as default")
    print("=" * 60)

    attempts = 0
    while attempts < 3:
        raw = input("\n  Enter month: ").strip()

        # Empty → default to current month
        if not raw:
            month_key = next(k for k, v in MONTH_MAP.items()
                             if MONTH_MAP[k]["period_num"] == (current_month - 1) or
                             (current_month == 1 and k == "january"))
            month_info = build_month_info(month_key)
            print(f"\n  No input — using current month: "
                  f"{month_info['webapp_month']} {month_info['webapp_year']}")
            print(f"  Soliq.uz tax period (Давр): "
                  f"{month_info['tax_period']} {month_info['tax_period_year']}")
            return month_info

        month_key = parse_month_input(raw)
        if not month_key:
            print(f"  Invalid: '{raw}'. Valid: January-December or 1-12")
            attempts += 1
            continue

        month_info = build_month_info(month_key)

        # Warn if future month (webapp_month number > current month)
        if month_info["prev_year"] is False:
            selected_month_num = month_info["period_num"] + 1
        else:
            selected_month_num = 1
        if selected_month_num > current_month:
            print(f"\n  WARNING: {month_info['webapp_month']} {month_info['webapp_year']} "
                  f"is in the future (current month is {MONTH_UZ[current_month]}).")
            print("  Reports for this period may not be submitted yet.")
            confirm = input("  Continue anyway? (y/n): ").strip().lower()
            if confirm not in ("y", "yes"):
                attempts += 1
                continue

        print(f"\n  Selected:          {month_info['webapp_month']} {month_info['webapp_year']}")
        print(f"  Soliq.uz Давр:     {month_info['tax_period']} {month_info['tax_period_year']}")
        confirm = input("\n  Proceed? (y/n): ").strip().lower()
        if confirm in ("y", "yes", ""):
            return month_info

        attempts += 1

    print("\n  Too many invalid attempts. Exiting.")
    raise SystemExit(1)


def log(msg):
    sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()

# ─── Monitoring app ───────────────────────────────────────────────────────────
async def mon_login(page):
    await page.goto("https://khan-accounting-static-app.netlify.app/login",
                    wait_until="domcontentloaded")
    await page.wait_for_selector("input[placeholder='Email']", timeout=8000)
    await page.locator("input[placeholder='Email']").press_sequentially(
        MONITORING_EMAIL, delay=40)
    await page.locator("input[type='password']").press_sequentially(
        MONITORING_PASSWORD, delay=40)
    await page.get_by_text("Войти").click()
    await asyncio.sleep(5)

async def go_to_tax_tab(page):
    """Navigate to monitoring page -> Налоговые отчёты inner tab."""
    await page.goto(MONITORING_URL, wait_until="domcontentloaded")
    await asyncio.sleep(2)
    if "/login" in page.url:
        await mon_login(page)
        await page.goto(MONITORING_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)
    inner = page.locator(".inner-tab")
    for i in range(await inner.count()):
        txt = await inner.nth(i).inner_text()
        if "алог" in txt:
            await inner.nth(i).click()
            await asyncio.sleep(1)
            break

async def navigate_to_webapp_month(page, month_info: dict):
    """
    Navigate monitoring web app to the correct month.
    Clicks Предыдущий/Следующий until webapp_month appears, or gives up after 12 clicks.
    """
    target = f"{month_info['webapp_month']} {month_info['webapp_year']}"
    log(f"  Navigating web app to: {target}")

    for _ in range(12):
        try:
            body_text = await page.inner_text("body")
        except:
            break
        if month_info["webapp_month"] in body_text:
            log(f"  Web app month confirmed: {target}")
            return

        # Try clicking previous/next month navigation buttons
        for btn_text in ["Предыдущий", "Следующий", "Prev", "Next", "<", ">"]:
            btn = page.get_by_text(btn_text, exact=False)
            if await btn.count() > 0:
                try:
                    await btn.first.click()
                    await asyncio.sleep(0.8)
                    break
                except:
                    continue
        else:
            break  # No navigation button found

    # Final check
    try:
        body_text = await page.inner_text("body")
        if month_info["webapp_month"] not in body_text:
            log(f"  WARNING: Could not navigate to {target} — proceeding with current month shown")
    except:
        pass


async def scrape_companies_from_monitoring(page):
    """Return list of (inn, name) from the monitoring table."""
    await go_to_tax_tab(page)
    companies = []
    rows = await page.query_selector_all("tr")
    for row in rows:
        txt = await row.inner_text()
        m = re.search(r"ИНН\s+(\d{9,12})", txt)
        if m:
            inn = m.group(1)
            # extract company name (first line before INN)
            name = txt.split("\n")[0].strip() or txt.split("ИНН")[0].strip()
            companies.append((inn, name))
    log(f"Found {len(companies)} companies in monitoring app")
    return companies

async def update_cell(page, inn, col_name, status_label, date_str):
    """Open cell modal for company+column and save status+date."""
    rows = await page.query_selector_all("tr")
    target_row = None
    for row in rows:
        if inn in (await row.inner_text()):
            target_row = row
            break
    if target_row is None:
        log(f"    Row not found for INN {inn}")
        return False

    row_tds = await target_row.query_selector_all("td")
    col_idx = COL_INDEX.get(col_name, -1)
    if col_idx < 0 or col_idx >= len(row_tds):
        log(f"    Col {col_name} not found (idx={col_idx}, tds={len(row_tds)})")
        return False

    await row_tds[col_idx].click()
    await asyncio.sleep(1)

    modal = page.locator(".modal-overlay")
    if await modal.count() == 0:
        log(f"    Modal did not open")
        return False

    # Select status
    status_btn = modal.get_by_text(status_label, exact=False)
    if await status_btn.count() > 0:
        await status_btn.first.click()
        await asyncio.sleep(0.3)

    # Set date
    date_inp = modal.locator("input").first
    if await date_inp.count() > 0:
        await date_inp.click(click_count=3)
        await date_inp.press_sequentially(date_str, delay=30)

    # Save
    save = modal.get_by_text("Сохранить")
    if await save.count() > 0:
        await save.click()
        await asyncio.sleep(0.8)
        return True
    else:
        await modal.get_by_text("Отмена").click()
        return False

# ─── E-IMZO / soliq.uz ───────────────────────────────────────────────────────
async def eimzo_call(ws, plugin, name, arguments=None):
    payload = {"plugin": plugin, "name": name}
    if arguments:
        payload["arguments"] = arguments
    await ws.send(json.dumps(payload))
    return json.loads(await ws.recv())

async def find_cert(inn):
    async with websockets.connect(
        EIMZO_WS,
        additional_headers={"Origin": "https://my.soliq.uz"}
    ) as ws:
        resp = await eimzo_call(ws, "pfx", "list_all_certificates")
        certs = resp.get("certificates", [])
        target = next(
            (c for c in certs
             if f"DS{inn}" in c.get("name", "").upper()
             or f"1.2.860.3.16.1.1={inn}" in c.get("alias", "")),
            None
        )
        if not target:
            return None, None, None
        cert_name = target["name"]
        cert_alias = target["alias"]
        m = re.search(r"[-. _]+([A-Za-z0-9]+)\s*$", cert_name)
        pin = m.group(1) if m else ""
        load = await eimzo_call(ws, "pfx", "load_key",
                                ["D:\\", "DSKEYS", cert_name, cert_alias])
        if load.get("status", -1) <= 0:
            return None, None, None
        return load.get("keyId"), pin, cert_name

def _type_pin(pin):
    """
    Type PIN reliably into whatever window currently has focus.
    Uses pyautogui.press() char-by-char — works with mixed alphanumeric PINs.
    Falls back to pyperclip paste if available.
    """
    try:
        import pyperclip
        pyperclip.copy(pin)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
    except ImportError:
        # pyperclip not installed — type char by char
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        for ch in pin:
            pyautogui.press(ch)
            time.sleep(0.04)


def _focus_and_type(hwnd_or_win, pin, use_win32=False):
    """
    Bring a window to the front, click its center, type PIN, press Enter.
    hwnd_or_win: int (win32 handle) or pygetwindow window object.
    """
    try:
        if use_win32:
            import win32gui, win32con
            win32gui.ShowWindow(hwnd_or_win, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd_or_win)
            time.sleep(0.5)
            rect = win32gui.GetWindowRect(hwnd_or_win)
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
        else:
            w = hwnd_or_win
            try:
                w.restore()
            except Exception:
                pass
            w.activate()
            time.sleep(0.5)
            cx = w.left + w.width  // 2
            cy = w.top  + w.height // 2

        # Click center to put focus on the PIN input field
        pyautogui.click(cx, cy)
        time.sleep(0.3)
        _type_pin(pin)
        time.sleep(0.2)
        pyautogui.press("enter")
        return True
    except Exception as e:
        log(f"    _focus_and_type error: {e}")
        return False


def handle_pin(pin, timeout=15):
    """
    Robustly handle the E-IMZO Java PIN dialog (external AWT window).

    Strategy order:
      1. win32gui — find Java AWT dialog by class name (most reliable)
      2. win32gui — find by title keyword
      3. pygetwindow — find by title keyword
      4. pygetwindow — find small untitled window (Java dialogs are often 1×1 or tiny)

    Retries every 0.5 s until timeout.
    """
    # Try to import win32gui (pywin32 package)
    try:
        import win32gui, win32con
        has_win32 = True
    except ImportError:
        has_win32 = False
        log("    [handle_pin] win32gui not available — using pygetwindow only")

    try:
        import pygetwindow as gw
        has_gw = True
    except ImportError:
        has_gw = False

    TITLE_KEYWORDS = ["imzo", "pin", "парол", "password", "калит", "kalit", "введите", "e-imzo"]
    # Java AWT/Swing window class names on Windows
    JAVA_CLASSES   = ["SunAwtDialog", "SunAwtFrame", "SunAwtWindow", "SALFRAME"]

    deadline = time.time() + timeout
    attempt  = 0

    while time.time() < deadline:
        attempt += 1

        # ── Strategy 1 & 2: win32gui enumeration ─────────────────────────────
        if has_win32:
            found_by_class = []
            found_by_title = []

            def _enum(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title      = win32gui.GetWindowText(hwnd).lower()
                class_name = win32gui.GetClassName(hwnd)
                rect       = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w < 10 or h < 10:          # ignore invisible/collapsed
                    return
                if class_name in JAVA_CLASSES:
                    found_by_class.append((hwnd, title, class_name, w, h))
                if any(k in title for k in TITLE_KEYWORDS):
                    found_by_title.append((hwnd, title, class_name, w, h))

            try:
                win32gui.EnumWindows(_enum, None)
            except Exception:
                pass

            # Prefer class match (definitive Java dialog)
            for hwnd, title, cls, w, h in found_by_class:
                log(f"    [attempt {attempt}] Java dialog by class: '{cls}' title='{title}' size={w}x{h}")
                if _focus_and_type(hwnd, pin, use_win32=True):
                    return True

            # Fall back to title keyword match via win32
            for hwnd, title, cls, w, h in found_by_title:
                log(f"    [attempt {attempt}] Java dialog by title: '{title}' class='{cls}'")
                if _focus_and_type(hwnd, pin, use_win32=True):
                    return True

        # ── Strategy 3 & 4: pygetwindow ──────────────────────────────────────
        if has_gw:
            try:
                wins = gw.getAllWindows()
            except Exception:
                wins = []

            # Strategy 3: by title keyword
            for w in wins:
                if any(k in w.title.lower() for k in TITLE_KEYWORDS):
                    log(f"    [attempt {attempt}] pygetwindow title match: '{w.title}'")
                    if _focus_and_type(w, pin, use_win32=False):
                        return True

            # Strategy 4: small untitled windows (Java dialogs often have no title)
            untitled = [
                w for w in wins
                if w.title == ""
                and 20 < w.width  < 900
                and 20 < w.height < 700
            ]
            # Sort smallest first — most likely to be a dialog, not a toolbar
            untitled.sort(key=lambda x: x.width * x.height)
            for w in untitled:
                log(f"    [attempt {attempt}] untitled window {w.width}x{w.height} @ ({w.left},{w.top})")
                if _focus_and_type(w, pin, use_win32=False):
                    return True

        time.sleep(0.5)

    log(f"    handle_pin: dialog not found after {timeout}s — giving up")
    return False

async def soliq_login(page, inn, pin, cert_name):
    await page.goto("https://my.soliq.uz/main/")
    await page.wait_for_load_state("networkidle")
    await page.get_by_text("Юридик шахслар учун").first.click()
    await page.wait_for_load_state("networkidle")
    await page.get_by_text("Кабинетга кириш").first.click()
    await page.wait_for_load_state("networkidle")
    await page.get_by_text("ESI орқали").first.click()
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)

    # Open dropdown and select cert
    dd = page.locator("[class*='chosen'], [class*='select2'], .dropdown, select").first
    if await dd.count() > 0:
        await dd.click()
        await asyncio.sleep(1)
    all_li = page.locator("li, [role='option']")
    for i in range(await all_li.count()):
        el = all_li.nth(i)
        try:
            if not await el.is_visible(): continue
            txt = await el.inner_text()
            if inn in txt or cert_name[:10] in txt:
                await el.click()
                break
        except: continue

    await asyncio.sleep(1)

    # ── CAPTCHA check ("Spamdan himoya") ─────────────────────────────────────
    # Appears between cert selection and Kirish button.
    # Browser is visible so user can see the image — just ask for the code.
    captcha_input = page.locator(
        "input[placeholder*='himoya'], "
        "input[placeholder*='Spam'], "
        "input[placeholder*='captcha'], "
        "input[placeholder*='kod'], "
        "input[placeholder*='raqam']"
    )
    await asyncio.sleep(0.5)
    if await captcha_input.count() > 0 and await captcha_input.first.is_visible():
        await page.screenshot(path=f"C:/Users/khanm/captcha_{inn}.png")
        log(f"  CAPTCHA detected for INN {inn}!")
        log(f"  Screenshot saved: C:/Users/khanm/captcha_{inn}.png")
        log(f"  >>> Look at the browser window and find the number in the image <<<")
        # Ask user to type code in terminal
        captcha_code = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input(f"  Enter CAPTCHA code for {inn}: ").strip()
        )
        await captcha_input.first.click()
        await captcha_input.first.fill(captcha_code)
        log(f"  CAPTCHA filled: {captcha_code}")
        await asyncio.sleep(0.5)
    else:
        log(f"  No CAPTCHA — proceeding directly")

    # ── Password field (if visible on page) ──────────────────────────────────
    pwd = page.locator("input[type='password'], input[placeholder*='arol']")
    for i in range(await pwd.count()):
        if await pwd.nth(i).is_visible():
            await pwd.nth(i).fill(pin)

    # ── Click Kirish ──────────────────────────────────────────────────────────
    kirish = page.get_by_role("button", name="Kirish")
    if await kirish.count() > 0:
        await kirish.first.click()
    else:
        await page.get_by_text("Kirish").first.click()
    log(f"  Kirish clicked")

    # ── Handle E-IMZO Java PIN dialog ─────────────────────────────────────────
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, handle_pin, pin, 20)

    try: await page.wait_for_load_state("networkidle", timeout=15000)
    except: pass
    await asyncio.sleep(3)
    await page.goto("https://my.soliq.uz/main/")
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(2)

WEFO_URL = "https://my.soliq.uz/wefo4-clientui/form/uz/reports/0"

async def _wait_for_table(page, timeout=15):
    """Wait until at least one <td> is visible in the page (SPA table rendered)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        count = await page.locator("table td").count()
        if count > 0:
            return True
        await asyncio.sleep(0.5)
    return False

async def scrape_reports(page, inn, tax_period: str):
    """
    Go to the tax reports page, click 'Қабул қилинган' filter,
    wait for the SPA table to render, then scrape rows where
    Давр column matches tax_period.

    Table columns (0-based):
      0  Харакатлар  (action icons)
      1  Ҳисобот №
      2  Пакет №     ← e.g. 11104_1 / 11101_20 / 10205_47 / 10006_45
      3  Номи
      4  Йил
      5  Давр        ← Апрел / Март / I квартал …
      6  Жўнатилган сана  ← dd.mm.yyyy hh:mm:ss
      7  Сертификат
      8  Холати
      …

    Returns {col_name: {"status": "accepted", "date": "dd.mm.yyyy"}}
    """

    # ── Step 1: navigate to reports page ─────────────────────────────────────
    log(f"    → {WEFO_URL}")
    await page.goto(WEFO_URL, wait_until="domcontentloaded", timeout=20000)

    # SPA: wait until the table actually renders (up to 15 s)
    rendered = await _wait_for_table(page, timeout=15)
    if not rendered:
        log(f"    WARNING: table did not render after 15s — taking screenshot anyway")
    await page.screenshot(path=f"C:/Users/khanm/sc_{inn}_main.png")
    log(f"    Current URL: {page.url}")

    # ── Step 2: click green "Қабул қилинган" filter button ───────────────────
    clicked_filter = False
    for kw in ["Қабул қилинган", "Қабул қил", "Принят"]:
        el = page.get_by_text(kw, exact=False)
        if await el.count() > 0:
            try:
                await el.first.click()
                await asyncio.sleep(1)
                # wait for table to re-render after filter click
                await _wait_for_table(page, timeout=10)
                log(f"    Filter clicked: '{kw}'")
                clicked_filter = True
                break
            except Exception:
                pass
    if not clicked_filter:
        log("    WARNING: could not click accepted-filter button — scraping all rows")

    await page.screenshot(path=f"C:/Users/khanm/sc_{inn}_accepted.png")

    # ── Step 3: read every <tr>, keep rows where Давр = tax_period ───────────
    results = {}
    rows = await page.query_selector_all("tr")
    log(f"    Rows in table: {len(rows)}")

    for row in rows:
        try:
            cells = await row.query_selector_all("td")
            if len(cells) < 6:
                continue

            # Read every cell's text
            texts = [( await c.inner_text() ).strip() for c in cells]

            # Column 5 = Давр  (case-insensitive partial match)
            davr = texts[5] if len(texts) > 5 else ""
            if tax_period.lower() not in davr.lower():
                continue

            # Column 2 = Пакет №  e.g. "11101_20"
            paket = texts[2] if len(texts) > 2 else ""
            paket = paket.strip()

            # Column 6 = Жўнатилган сана  e.g. "14.05.2026 11:21:15"
            # extract only the date part dd.mm.yyyy
            date_raw = texts[6] if len(texts) > 6 else ""
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", date_raw)
            date_str = m.group(1) if m else ""

            if not paket or not date_str:
                continue

            # Map Пакет № prefix → monitoring column name
            for prefix, col in REPORT_TO_COL.items():
                if paket.startswith(prefix):
                    results[col] = {"status": "accepted", "date": date_str}
                    log(f"    ✓ {paket}  Давр={davr}  date={date_str}  → {col}")
                    break

        except Exception:
            continue

    log(f"    Done: {len(results)} report(s) for period '{tax_period}': {list(results.keys())}")
    return results

# ─── Process one company ──────────────────────────────────────────────────────
async def process_company(playwright, mon_page, inn, name, month_info: dict):
    log(f"\nProcessing {name} (INN: {inn})...")

    # Find cert
    try:
        key_id, pin, cert_name = await find_cert(inn)
    except Exception as e:
        log(f"  E-IMZO error: {e}")
        return {"inn": inn, "name": name, "status": "error", "error": str(e), "cols": {}}

    if key_id is None:
        log(f"  No cert found for {inn} - skipping")
        return {"inn": inn, "name": name, "status": "no_cert", "cols": {}}

    # Login to soliq.uz and scrape
    browser = await playwright.chromium.launch(headless=False)
    ctx = await browser.new_context(ignore_https_errors=True)
    page = await ctx.new_page()
    tax_data = {}
    try:
        await soliq_login(page, inn, pin, cert_name)
        tax_data = await scrape_reports(page, inn, month_info["tax_period"])
        log(f"  Scraped {len(tax_data)} reports")
    except Exception as e:
        log(f"  Soliq error: {e}")
    finally:
        await browser.close()

    if not tax_data:
        return {"inn": inn, "name": name, "status": "no_data", "cols": {}}

    # Update monitoring app
    await go_to_tax_tab(mon_page)
    updated = {}
    for col_name, info in tax_data.items():
        date_str = info.get("date", datetime.datetime.now().strftime("%d.%m.%Y"))
        ok = await update_cell(mon_page, inn, col_name, "Готово", date_str)
        updated[col_name] = "OK" if ok else "FAIL"
        log(f"  {col_name}: {'OK' if ok else 'FAIL'}")

    log(f"  Completed: {name}")
    return {"inn": inn, "name": name, "status": "ok", "cols": updated}

# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    # Ask user for month BEFORE opening any browser
    month_info = get_month_from_user()
    month_label = f"{month_info['webapp_month']} {month_info['webapp_year']}"
    tax_label   = f"{month_info['tax_period']} {month_info['tax_period_year']}"

    log(f"\nSoliq.uz Tax Monitor")
    log(f"  Monitoring month : {month_label}")
    log(f"  Soliq.uz period  : {tax_label}")
    log("=" * 50)

    async with async_playwright() as p:
        # ── Login to monitoring app once ──
        log("Logging into monitoring app...")
        mon_browser = await p.chromium.launch(headless=False)
        mon_page = await mon_browser.new_page(viewport={"width": 1440, "height": 900})
        await mon_login(mon_page)
        await go_to_tax_tab(mon_page)
        await navigate_to_webapp_month(mon_page, month_info)

        # ── Scrape company list ──
        companies = await scrape_companies_from_monitoring(mon_page)
        if not companies:
            log("No companies found in monitoring app! Using fallback list.")
            companies = [
                ("312035036", "LAIDA HOTEL"),
                ("309338537", "ECO NEST UNO"),
                ("310575370", "GOLDEN SILK ROAD ENERGY"),
                ("310981469", "SIX WINGS INDUSTRIAL"),
                ("311133152", "SENYOU INTERNATIONAL"),
                ("312246153", "TERRA NOVA PROJECT"),
            ]

        log(f"\nWill process {len(companies)} companies:")
        for inn, name in companies:
            log(f"  {name} (INN: {inn})")

        # ── Process each company ──
        results = []
        for inn, name in companies:
            try:
                result = await process_company(p, mon_page, inn, name, month_info)
                results.append(result)
            except Exception as e:
                log(f"  ERROR {name}: {e}")
                results.append({"inn": inn, "name": name, "status": "error",
                                 "error": str(e), "cols": {}})

        await mon_browser.close()

        # ── Summary ──
        log(f"\n{'='*48}")
        log(f" BATCH ANALYSIS COMPLETE - {month_label}  (Давр: {tax_label})")
        log(f"{'='*48}")
        log(f" {'COMPANY':<30} STATUS")
        log(f" {'-'*46}")

        ok_count = 0
        issue_count = 0
        for r in results:
            name = r["name"][:28]
            st = r["status"]
            cols = r.get("cols", {})
            ok_cols = sum(1 for v in cols.values() if v == "OK")
            total_cols = len(cols)

            if st == "no_cert":
                label = "NO CERT"
                issue_count += 1
            elif st == "error":
                label = f"ERROR"
                issue_count += 1
            elif st == "no_data":
                label = "NO DATA"
                issue_count += 1
            elif ok_cols == total_cols and total_cols > 0:
                label = f"OK {ok_cols}/{total_cols}"
                ok_count += 1
            elif total_cols > 0:
                missing = [k for k, v in cols.items() if v != "OK"]
                label = f"PARTIAL {ok_cols}/{total_cols} (missing: {', '.join(missing[:2])})"
                issue_count += 1
            else:
                label = "SKIPPED"
                issue_count += 1

            log(f" {name:<30} {label}")

        log(f"{'='*48}")
        log(f" Total: {len(results)} | OK: {ok_count} | Issues: {issue_count}")
        log(f"{'='*48}\n")

if __name__ == "__main__":
    asyncio.run(main())
