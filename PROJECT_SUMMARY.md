# Soliq.uz Tax Monitoring Automation

## What this project does
Fully automated tax report monitoring for multiple companies.
- Logs into `my.soliq.uz` using EDS (digital signature) keys
- Scrapes accepted tax reports filtered by selected month
- Updates the monitoring web app (`khan-accounting-static-app.netlify.app`) automatically

---

## How to run

```bash
cd C:\Users\khanm
python soliq_monitor.py
```

**What happens:**
1. Script asks which month to update (e.g. `May`, `май`, `5`)
2. Opens browser, logs into monitoring web app
3. For each of 6 companies:
   - Logs into my.soliq.uz via EDS cert
   - Goes to `/wefo4-clientui/form/uz/reports/0`
   - Clicks "Қабул қилинган" filter
   - Extracts reports where Давр = tax period (e.g. May → Апрел)
   - Updates monitoring app cells with status + date
4. Prints summary table

---

## Files

| File | Purpose |
|------|---------|
| `soliq_monitor.py` | **Main script** — full batch automation |
| `soliq_login.py` | Basic single-INN login only |
| `PROJECT_SUMMARY.md` | This file |

---

## Month Selection Logic

> Taxes for Month N are submitted in Month N+1

| User types | Web app shows | Soliq.uz Давр |
|-----------|---------------|---------------|
| May / май / 5 | Май 2026 | Апрел |
| April / апрел / 4 | Апрел 2026 | Март |
| January / 1 | Январь 2026 | Декабрь 2025 |

Supports: English / Russian / Uzbek / numbers 1–12

---

## Report → Monitoring Column Mapping

| Пакет № prefix | Column in monitoring app |
|---------------|--------------------------|
| 11101 | НДФЛ 15 |
| 10006 | НДС 20 |
| 11104 | ПРИБЫЛЬ 20 |
| 10205 | АВАНСОВЫЙ |

---

## Technical Details

### Key file location
- Folder: `D:\DSKEYS\`
- Format: `DS{INN}{sequence} {PIN}.pfx`
- PIN = last alphanumeric chunk in the filename

### E-IMZO (Electronic Signature System)
- Local Java app on `ws://127.0.0.1:64646/service/cryptapi`
- `pfx.list_all_certificates` → find cert by INN
- `pfx.load_key [disk, "DSKEYS", cert_name, cert_alias]` → load key
- Java PIN dialog handled via win32gui (4 strategies)

### Login flow (ESI OAuth2)
1. `my.soliq.uz` → click "Юридик шахслар учун" → "Кабинетга кириш" → "ESI орқали"
2. Select cert from dropdown by INN
3. **CAPTCHA** ("Spamdan himoya") — if shown, script pauses and asks user to type code
4. Click "Kirish"
5. E-IMZO Java PIN dialog → auto-filled via win32gui + clipboard paste
6. Navigate to `my.soliq.uz/main/`

### PIN dialog handling (4-strategy fallback)
1. `win32gui` → Java class name (`SunAwtDialog`, `SunAwtFrame`)
2. `win32gui` → window title keyword
3. `pygetwindow` → title keyword
4. `pygetwindow` → small untitled window
→ Clicks center of dialog → pastes PIN via clipboard → Enter

### Important findings
- Must use `ignore_https_errors=True` in Playwright
- After ESI auth navigate to main manually (`client-error` redirect is normal)
- Reports page URL: `https://my.soliq.uz/wefo4-clientui/form/uz/reports/0`
- Page is a React SPA — must wait for table to render before scraping
- CAPTCHA appears randomly between cert selection and Kirish click

---

## Companies (6)

| Company | INN |
|---------|-----|
| ECO NEST UNO | 309338537 |
| GOLDEN SILK ROAD ENERGY | 310575370 |
| SIX WINGS INDUSTRIAL | 310981469 |
| SENYOU INTERNATIONAL | 311133152 |
| LAIDA HOTEL | 312035036 |
| TERRA NOVA PROJECT | 312246153 |

---

## Dependencies

```bash
pip install playwright websockets pyautogui pygetwindow pywin32 pyperclip
playwright install chromium
```

---

## GitHub
https://github.com/AyubkhonA/soliq-login

---

## Monitoring Web App
- URL: https://khan-accounting-static-app.netlify.app/monitoring
- Login: worker1@test.com / Test12345
