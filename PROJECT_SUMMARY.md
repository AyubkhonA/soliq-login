# Soliq.uz Auto-Login Project

## What this project does
Automatically logs in to https://my.soliq.uz using a company INN number.
No manual clicking needed — fully automated.

---

## User Instructions (what was asked)

1. Open Chrome and go to https://my.soliq.uz/main/
2. Login using EDS keys (PFX files) stored in D:\DSKEYS folder
3. Login flow:
   - Click "Юридик шахслар учун"
   - Click "Кабинетга кириш"
   - Choose "ESI орқали" mode
   - Select cert by INN from dropdown
   - Handle E-IMZO Java PIN dialog automatically
   - Navigate to main page after login
4. INN entered by user when script runs (not hardcoded)
5. PIN = last alphanumeric chunk in the .pfx filename (e.g. DS312003147xxxx-**AAaa123456**.pfx)

---

## Technical Details

### Key file location
- Folder: `D:\DSKEYS\`
- Format: `DS{INN}{sequence}-{PIN}.pfx`
- Example: `DS3120031470002        AAaa123456.pfx`

### E-IMZO (Electronic Signature System)
- Local Java app running WebSocket on `ws://127.0.0.1:64646/service/cryptapi`
- Use `pfx.list_all_certificates` to get all certs
- Use `pfx.load_key` with `[disk, "DSKEYS", cert_name, cert_alias]` to load key
- The 4th argument must be the FULL alias (DN string), NOT the filename
- Java PIN dialog appears as untitled 1x1 window — handled via pyautogui

### Login flow (ESI OAuth2)
1. my.soliq.uz → auth?user_type=1
2. Redirects to esi.uz/oauth2/authorize
3. Select cert from dropdown (matches by INN)
4. Click "Kirish"
5. E-IMZO Java dialog appears → auto-type PIN → Enter
6. ESI signs challenge → POST to signin3
7. Callback → client-error (normal) → navigate to my.soliq.uz/main/
8. Logged in!

### Important findings
- Must use `ignore_https_errors=True` in Playwright (E-IMZO uses self-signed SSL cert on wss://127.0.0.1:64443)
- After ESI auth, must manually navigate to https://my.soliq.uz/main/ again
- `client-error` redirect is normal — just navigate to main after it
- E-IMZO Java PIN dialog = untitled window 1x1 pixels

---

## Files

| File | Location | Purpose |
|------|----------|---------|
| soliq_login.py | C:\Users\khanm\ | Main script |
| soliq_login.py | C:\Users\khanm\soliq-login\ | GitHub copy |

## GitHub
https://github.com/AyubkhonA/soliq-login

---

## How to run

```bash
cd C:\Users\khanm
python soliq_login.py
# Enter INN: 312035036
```

## Dependencies
```bash
pip install playwright websockets pyautogui websocket-client
playwright install chromium
```

---

## INNs tested
- 312035036 — LAIDA HOTEL MANAGEMENT (cert: DS3120350360002, PIN: 23136352) ✅
- 312003147 — FU PENG MCHJ (cert: DS3120031470002, PIN: AAaa123456) ✅

---

## What's left / next steps
- User wants to continue building more features after login
- Possible next: automate tasks inside the cabinet after login
