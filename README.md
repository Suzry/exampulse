# Exampulse

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC)](https://docs.pytest.org/)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-D7FF64)](https://docs.astral.sh/ruff/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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

## Your exam schedule

Exampulse reads your exams from a local `exams.json` file (it is gitignored, so
your real schedule never leaves your machine). Create one shaped like this:

```json
[
  {
    "course": "Operating Systems",
    "exam_at": "2026-06-22T10:15:00+03:00",
    "grade": null,
    "letter_grade": null,
    "notes": "Code: CS2016; Room: 12-0.003"
  }
]
```

`exam_at` is an ISO-8601 timestamp with your local UTC offset. `grade`,
`letter_grade`, and `notes` are optional. Re-importing the same `course` +
`exam_at` updates that exam in place (e.g. to fill in a grade once the
result is out). Then import it:

```bash
exampulse exams import exams.json
```

You can also import straight from a calendar. If your university publishes the
exam schedule as iCal (or you keep it in Google/Apple Calendar), export it as
`.ics` and import it directly — `SUMMARY` becomes the course, `LOCATION` and
`DESCRIPTION` land in notes:

```bash
exampulse exams import exam-schedule.ics
```

All-day events and events without timezone information are rejected rather
than silently guessed, because a wrong exam time corrupts every night match.

If you just want to try the tool without WHOOP or a schedule, run
`exampulse demo-seed` to generate 30 days of realistic offline data plus a
small demo exam schedule.

## Commands

```powershell
exampulse auth
exampulse demo-seed
exampulse sync --days 30
exampulse sync --days 30 --streams
exampulse whoop raw-check
exampulse whoop import-export my_whoop_data.zip
exampulse whoop exam-hr
exampulse apple import-export export.zip
exampulse exams import exams.json
exampulse exams import exam-schedule.ics
exampulse exams list
exampulse exams list --json
exampulse today --compact
exampulse plan
exampulse week --days 7
exampulse report
exampulse report --classic
exampulse report --json
exampulse correlate
exampulse semester --output semester-report.md
exampulse export
exampulse watch --every 30
exampulse --version
```

`--json` on `report` and `exams list` prints machine-readable JSON to stdout
instead of the terminal dashboard, for scripting or feeding into another tool.

## Planning ahead: `plan` and `week`

The report tells you what happened; `plan` tells you what to do **tonight**.
For every upcoming exam it derives, from your own baseline:

- a **target sleep** (your baseline median, not a universal 8h rule),
- a **bedtime / wake target** that leaves a prep buffer before the exam
  (default 2h, tune with `--prep-buffer`),
- how that bedtime compares to your **usual** bedtime,
- a **readiness projection** assuming tonight matches your baseline and your
  recent 3-night recovery/HRV/RHR trend holds — labeled as a projection, never
  as a measurement.

The exam that needs tonight's sleep is tagged `TONIGHT`. `exampulse week`
shows the same schedule as a day-by-day view and both commands flag **short
recovery windows** — back-to-back exams where the night between them cannot
physically fit your target sleep, so you know to bank sleep the night before.

## Grades vs physiology: `correlate` and `semester`

Once results are in, fill `grade` in `exams.json` and re-import. Then:

- `exampulse correlate` pairs each graded exam with its night-before readiness,
  stress index, and sleep debt, computes Pearson and Spearman correlations, and
  draws a terminal scatter plot. With few exams it says so loudly — small
  samples are labeled as anecdotes, not evidence.
- `exampulse semester` writes a markdown end-of-term report: average readiness,
  best/worst night, recurring pressure points (e.g. "low sleep — 4 exams"),
  the grade correlations, and method notes. Use `--stdout` to print instead of
  writing a file.

## Apple Watch instead of WHOOP

Exampulse can run entirely from an Apple Health export — no WHOOP account
needed. On the iPhone: Health app → profile picture → *Export All Health
Data*, then:

```bash
exampulse apple import-export export.zip   # or the unzipped folder / export.xml
exampulse exams import exams.json
exampulse report
```

Sleep sessions (with REM/deep/core stages when the watch provides them),
nightly HRV, and resting heart rate are imported into the same tables the
report reads. Overlapping watch + phone records are merged, not double-counted,
and re-importing is idempotent. Two honest caveats: Apple has no recovery
score (that readiness component is skipped and the weights renormalize), and
Apple reports HRV as SDNN while WHOOP uses RMSSD — absolute values differ, but
deltas against your own baseline from the same source stay meaningful.

## Reading the report

By default `exampulse report` prints a tight, scannable dashboard — no noise:

- `[whoop]` / `[exams]` / `[run]` process lines summarize the run.
- **EXAM STRESS** — one borderless, color-coded row per exam (ranked by
  stress): the Physiological Stress Index, hours **awake** before the exam,
  sleep vs baseline, recovery, HRV delta, and an inline stress bar.
- **READINESS** — a diverging bar chart of each exam's readiness around the 50
  midpoint (green = ready, red = low).

Add `--full` for the deep view: per-metric `median ± σ̂`, deltas, 14-day **trend
sparklines**, **z-scores** and percentile ranks, the **stress-driver** breakdown,
and all heart-rate signals (`awake`, `night arousal`, `pre-exam`, `hr/min`,
`NIGHT HR SIGNAL`).

```bash
exampulse report           # brief one-table view
exampulse report --full    # full per-exam detail
exampulse report --classic # plain boxed layout
```

The night-before sleep is excluded from its own baseline so the comparison is
against the *other* nights. Baselines use the **median** and a MAD-based sigma
rather than mean ± std, so a single all-nighter inside the window can't drag
your baseline around. When the baseline has fewer than 5 nights, z-scores and
percentiles are withheld (the deltas still show) instead of pretending thin
data is statistically confident. Both knobs are configurable via environment
variables: `EXAMPULSE_BASELINE_DAYS` (default 14) and
`EXAMPULSE_MIN_BASELINE_NIGHTS` (default 5). Colors and box-drawing degrade
gracefully to ASCII on terminals without UTF-8.

## Heart rate during the exam (WHOOP data export)

WHOOP's official data export does **not** contain a minute-by-minute heart-rate
timeline — not for sleep and not for activities. The standard export
(`workouts.csv`, `physiological_cycles.csv`, `sleeps.csv`) only carries
summaries. So there is no honest way to reconstruct a per-minute exam HR trace.

There is, however, one workable path: **start a WHOOP "Activity" at the start of
the exam and end it when you leave.** WHOOP then records that activity with an
**average HR, max HR, and an HR-zone distribution** for exactly that window.
Import the export and match it to your exams:

```bash
# PATH can be the export .zip, the unzipped folder, or a single CSV file.
exampulse whoop import-export my_whoop_data.zip
exampulse whoop exam-hr
```

## Offline mode from the WHOOP export (no API, no ngrok)

`whoop import-export` also reads `physiological_cycles.csv` (per-day recovery
score, RHR, HRV, strain) and `sleeps.csv` (the complete, authoritative sleep list
including naps and any main sleeps that the cycles file omits). That is
everything the readiness report needs, so you can run Exampulse entirely from the
export without WHOOP OAuth, `sync`, or ngrok:

```bash
# --replace clears any previously synced WHOOP data for a clean baseline.
exampulse whoop import-export my_whoop_data.zip --replace
exampulse exams import exams.json
exampulse report
```

Imported rows use synthesized, date-stable IDs, so re-importing the same export
is idempotent. Note these are per-day summaries; `physiological_cycles.csv` does
not contain a minute-by-minute heart-rate timeline (see below).

`exam-hr` lists each exam with the average/max HR and zone bar from any logged
activity that overlaps the exam window, plus a `Covers` column showing how much
of the window the activity spanned. Exams with no logged activity are reported
honestly as `no activity logged` — never a fabricated number. The same line also
appears in `exampulse report` under each exam's detail.

WHOOP export timestamps are in your local timezone (the `Cycle timezone` column),
and the importer converts them to UTC for matching. Re-importing is idempotent.

## Per-minute exam heart rate (bring your own data)

If you obtain a **per-minute heart-rate** stream (a `timestamp,hr` series), Exampulse
matches it to each exam window and reports the stress signal directly in the
report: average HR, delta vs the 90-minute pre-exam baseline, a z-score, the
percent of the window with elevated HR, and a minute-by-minute sparkline.

```bash
# Columns are auto-detected (timestamp/time/datetime, hr/bpm/heart rate, ...).
# Use --timestamp-col / --hr-col to override.
exampulse research raw-hr import-csv my_hr.csv --source my_device
exampulse report                       # adds an "hr/min" line under each exam
exampulse research raw-hr exam-window --exam "Operating Systems"
```

The same imported per-minute HR also feeds the **night-before** sleep analysis.
The official WHOOP Sleep Stream is often blocked (HTTP 403), so when imported
points fall inside the night-before sleep window, the report's `NIGHT HR SIGNAL`
section fills from them instead — average/max sleep HR, delta vs the sleep
baseline, elevated %, and spikes (labeled `source: imported per-minute HR`). Each
exam also gets a one-line **night arousal** verdict that combines resting-HR
elevation with HRV suppression — the classic pre-exam stress signature.

Where the per-minute data comes from is up to you. WHOOP's **official** API and
data export do not expose an all-day HR timeline (see below), so this requires a
source you control — e.g. a chest strap / watch worn during the exam, or a
WHOOP per-minute stream obtained through unofficial means. Pulling WHOOP's
internal (non-public) API violates WHOOP's Terms of Service; that is your call to
make for your own account and data. Exampulse only ingests a CSV you provide and
never fabricates points.

## All-nighters before exams

If you pull all-nighters, there is no real night-before sleep, and WHOOP's "last
sleep" before the exam is actually a daytime nap from a day or two earlier. The
report handles this honestly:

- An **`awake`** line shows hours since your last real sleep (e.g.
  `44h awake — 24h+ no sleep`) and flags when the "sleep" metrics above are a nap
  rather than night-before sleep.
- A **`pre-exam`** line summarizes your heart rate over a fixed clock window
  (default 10 hours) before the exam start — the awake, studying hours — when you
  have imported per-minute HR. This replaces a meaningless "sleep window" for
  exams where you never slept.

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

## Development

```bash
pip install -e ".[dev]"
pytest          # run the test suite (includes hypothesis property tests)
ruff check .    # lint
mypy app        # type-check
```

CI runs all three on Python 3.11–3.13. `requirements.lock` is a pip-freeze
snapshot of a known-good environment; regenerate it after dependency changes
with `pip freeze --exclude-editable > requirements.lock`.

Scoring weights, stress thresholds, and baseline settings all live in one
place: `app/core/scoring.py` (`ScoringConfig`). The CLI in `app/cli/main.py`
is thin; all terminal rendering lives in `app/cli/views/`.

## License

Released under the [MIT License](LICENSE).
