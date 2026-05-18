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
        await date_inp.triple_click()
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

def handle_pin(pin, timeout=10):
    import pygetwindow as gw
    deadline = time.time() + timeout
    while time.time() < deadline:
        wins = gw.getAllWindows()
        for w in wins:
            if any(k in w.title.lower() for k in ["imzo", "pin", "парол", "password"]):
                try: w.activate()
                except: pass
                time.sleep(0.4)
                pyautogui.hotkey("ctrl", "a")
                pyautogui.typewrite(pin, interval=0.05)
                pyautogui.press("enter")
                return True
        small = [w for w in wins if w.title == "" and w.width < 600 and w.height < 400]
        if small:
            try: small[0].activate()
            except: pass
            time.sleep(0.4)
            pyautogui.typewrite(pin, interval=0.05)
            pyautogui.press("enter")
            return True
        time.sleep(0.5)
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
    # Fill password if shown
    pwd = page.locator("input[type='password'], input[placeholder*='arol']")
    for i in range(await pwd.count()):
        if await pwd.nth(i).is_visible():
            await pwd.nth(i).fill(pin)

    kirish = page.get_by_role("button", name="Kirish")
    if await kirish.count() > 0:
        await kirish.first.click()
    else:
        await page.get_by_text("Kirish").first.click()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, handle_pin, pin, 10)

    try: await page.wait_for_load_state("networkidle", timeout=15000)
    except: pass
    await asyncio.sleep(3)
    await page.goto("https://my.soliq.uz/main/")
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(2)

async def scrape_reports(page, inn):
    """Scrape accepted tax reports for current month. Returns {col: {date, status}}"""
    now = datetime.datetime.now()
    cur_month_name = MONTH_UZ[now.month]

    # Try to find tax reports section
    navigated = False
    for kw in ["Ҳисоблар ҳисоботлар", "ҳисобот", "Хисоб", "қабул"]:
        el = page.get_by_text(kw, exact=False)
        if await el.count() > 0:
            try:
                await el.first.click()
                await asyncio.sleep(2)
                navigated = True
                log(f"    Clicked: {kw}")
                break
            except: continue

    if not navigated:
        # Try direct URLs
        for path in ["/hisob-report", "/reports", "/report"]:
            try:
                r = await page.goto(f"https://my.soliq.uz{path}", timeout=5000)
                if r and r.status < 400:
                    navigated = True
                    break
            except: continue

    await page.screenshot(path=f"C:/Users/khanm/sc_{inn}_tile.png")

    # Click "Қабул қилинган" filter
    for kw in ["Қабул қилинган", "кабул", "Принят"]:
        el = page.get_by_text(kw, exact=False)
        if await el.count() > 0:
            try:
                await el.first.click()
                await asyncio.sleep(2)
                break
            except: pass

    await page.screenshot(path=f"C:/Users/khanm/sc_{inn}_accepted.png")

    results = {}
    rows = await page.query_selector_all("tr")
    for row in rows:
        try:
            cells = await row.query_selector_all("td")
            if len(cells) < 4: continue
            texts = [await c.inner_text() for c in cells]
            full = " ".join(texts)
            if cur_month_name not in full and str(now.year) not in full:
                continue

            # Find report type code
            paket = ""
            for txt in texts:
                m = re.search(r"\b(\d{4,5}_\d+)\b", txt)
                if m:
                    paket = m.group(1)
                    break
            if not paket:
                continue

            # Find date
            date_str = ""
            for txt in texts:
                m = re.search(r"(\d{2}\.\d{2}\.\d{4})", txt)
                if m:
                    date_str = m.group(1)
                    break

            # Map to column
            for prefix, col in REPORT_TO_COL.items():
                if paket.startswith(prefix) and date_str:
                    results[col] = {"status": "accepted", "date": date_str}
                    log(f"    {paket} -> {col} ({date_str})")
                    break
        except: continue

    return results

# ─── Process one company ──────────────────────────────────────────────────────
async def process_company(playwright, mon_page, inn, name):
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
        tax_data = await scrape_reports(page, inn)
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
    now = datetime.datetime.now()
    month_label = f"{MONTH_UZ[now.month]} {now.year}"
    log(f"\nSoliq.uz Tax Monitor - {month_label}")
    log("=" * 50)

    async with async_playwright() as p:
        # ── Login to monitoring app once ──
        log("Logging into monitoring app...")
        mon_browser = await p.chromium.launch(headless=False)
        mon_page = await mon_browser.new_page(viewport={"width": 1440, "height": 900})
        await mon_login(mon_page)
        await go_to_tax_tab(mon_page)

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
                result = await process_company(p, mon_page, inn, name)
                results.append(result)
            except Exception as e:
                log(f"  ERROR {name}: {e}")
                results.append({"inn": inn, "name": name, "status": "error",
                                 "error": str(e), "cols": {}})

        await mon_browser.close()

        # ── Summary ──
        log(f"\n{'='*48}")
        log(f" BATCH ANALYSIS COMPLETE - {month_label}")
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
