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
exampulse demo-seed
exampulse sync --days 30
exampulse exams import exams.json
exampulse exams list
exampulse report
exampulse watch --every 30
```

For offline exploration, run `exampulse demo-seed` first. It generates 30 days
of realistic WHOOP-like sleep, recovery, HRV, RHR, and cycle data, plus a small
demo exam schedule. Reports generated from this seeded dataset are labeled
`DEMO DATA`. This does not touch the real WHOOP OAuth or sync flow.

## Notes

This project uses the official WHOOP Developer API v2. The first milestone is
sleep, recovery, and cycle sync. Workout sync and a FastAPI/Next.js dashboard are
natural follow-ups because the CLI only calls service-layer code.
