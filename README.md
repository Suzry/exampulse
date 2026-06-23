# Exampulse

CLI-first WHOOP exam readiness analyzer. It syncs official WHOOP sleep, recovery,
and cycle data into SQLite, imports your exam schedule, then produces a terminal
report that compares the night before each exam against your baseline.

## Setup

Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS/Linux:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
```

## WHOOP OAuth setup

Create a WHOOP app in the Developer Dashboard:

```text
https://developer-dashboard.whoop.com
```

The app needs a public HTTPS redirect URL. For local development, start an
ngrok tunnel to Exampulse's callback server:

```bash
ngrok http 8711
```

Copy the HTTPS forwarding URL from ngrok and add `/callback` to the end. Use
that exact value as the redirect URI in the WHOOP Developer Dashboard and in
your local `.env`:

```text
WHOOP_REDIRECT_URI=https://your-ngrok-url.ngrok-free.dev/callback
```

Then fill in `WHOOP_CLIENT_ID` and `WHOOP_CLIENT_SECRET` in `.env` from the
WHOOP app credentials.

Run the first-time authorization while ngrok is still running:

```bash
exampulse auth
```

After authorization succeeds, normal daily commands do not need ngrok unless
you re-run `exampulse auth`.

## Commands

```powershell
exampulse auth
exampulse demo-seed
exampulse sync --days 30
exampulse sync --days 30 --streams
exampulse whoop raw-check
exampulse exams import exams.json
exampulse exams list
exampulse today --compact
exampulse report
exampulse report --classic
exampulse export
exampulse watch --every 30
```

## WHOOP-only raw HR status

- Exampulse uses official WHOOP APIs.
- Summary data works: sleep, recovery, cycles, strain, HRV, RHR.
- Official raw sleep HR is attempted through Sleep Stream.
- If WHOOP returns 403, Exampulse will not fake data.
- All-day raw HR is not available from the public WHOOP API.

For user-owned WHOOP raw HR exports, import a CSV with `timestamp` and `hr`:

```bash
python -m app.cli.main research raw-hr import-csv whoop_hr.csv --source whoop_export
```

## Daily usage on Windows

Open PowerShell in the project root and run:

```powershell
.\status.ps1
```

The script uses `.\.venv\Scripts\python.exe` directly, syncs WHOOP data, shows
today's compact next-exam status, and prints the compact report. ngrok is only
needed when running `exampulse auth` again. Normal daily usage does not require
ngrok.

## Daily usage on macOS/Linux

Open Terminal in the project root and run:

```bash
chmod +x ./status.sh
./status.sh
```

The script uses `./.venv/bin/python` directly, syncs WHOOP data, shows today's
compact next-exam status, and prints the compact report.

If you move this project from Windows to macOS, do not reuse the Windows
`.venv` folder. Create a fresh virtual environment on the Mac, then copy or
recreate `.env`. You can copy `exampulse.db` if you want to keep the local
SQLite data.

For offline exploration, run `exampulse demo-seed` first. It generates 30 days
of realistic WHOOP-like sleep, recovery, HRV, RHR, and cycle data, plus a small
demo exam schedule. Reports generated from this seeded dataset are labeled
`DEMO DATA`. This does not touch the real WHOOP OAuth or sync flow.

## Notes

This project uses the official WHOOP Developer API v2. The first milestone is
sleep, recovery, and cycle sync. Workout sync and a FastAPI/Next.js dashboard are
natural follow-ups because the CLI only calls service-layer code.
