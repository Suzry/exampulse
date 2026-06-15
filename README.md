# Exampulse

CLI-first WHOOP exam readiness analyzer. It syncs official WHOOP sleep, recovery,
and cycle data into SQLite, imports your exam schedule, then produces a terminal
report that compares the night before each exam against your baseline.

## Setup

```powershell
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Create a WHOOP app in the Developer Dashboard and set the redirect URI to:

```text
http://localhost:8711/callback
```

Then fill in `WHOOP_CLIENT_ID` and `WHOOP_CLIENT_SECRET` in `.env`.

## Commands

```powershell
exampulse auth
exampulse sync --days 30
exampulse exams import exams.json
exampulse exams list
exampulse report
exampulse watch --every 30
```

If the database is empty, `exampulse report` shows a clearly marked demo report
so the terminal UI can be checked before WHOOP credentials are configured.

## Notes

This project uses the official WHOOP Developer API v2. The first milestone is
sleep, recovery, and cycle sync. Workout sync and a FastAPI/Next.js dashboard are
natural follow-ups because the CLI only calls service-layer code.
