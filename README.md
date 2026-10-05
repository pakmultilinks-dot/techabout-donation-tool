# TechAbout Foundation: Donation Report Generator

A small Python (Flask) web tool. Upload a CSV of contributions and get a clean
summary report: total collected, number of contributors, average, progress
toward a target, a per-day contributions chart, and a contributor list with a
privacy mode that hides individual names and amounts. Reports download as PDF
or HTML.

## What it does

- CSV upload with validation: required columns are `name`, `amount`, `date`.
  Bad rows are listed with the row number and reason, and skipped; good rows
  still build the report.
- Summary stats: total, contributor count, average, progress bar toward a
  configurable target.
- Bar chart of contributions per day (matplotlib).
- Privacy toggle: names and amounts hidden by default ("Donor 1", "Hidden");
  one click reveals them.
- Downloadable report in PDF (reportlab) or HTML, respecting the privacy setting.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then edit SECRET_KEY
python app.py
```

Open http://127.0.0.1:5000 and upload `static/sample_donations.csv` to try it.

Environment variables (see `.env.example`):

- `SECRET_KEY`: Flask session secret. Change this in production.
- `TARGET_AMOUNT`: default fundraising target (default 100000).
- `PORT`: port to listen on (default 5000).

## CSV format

```csv
name,amount,date
Ahmed Khan,5000,2026-09-15
Sara Malik,2500,2026-09-16
```

Dates accept `YYYY-MM-DD` (also `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`,
`YYYY/MM/DD`). Amounts must be positive numbers. Column names are
case-insensitive.

## How it was tested

21 automated tests in `tests/test_app.py`, run with:

```bash
pytest -v
```

Coverage: CSV parsing (valid file, missing columns, empty file), row-level
validation (bad amount, negative amount, bad date, empty name, mixed
good/bad rows, alternate date format), statistics (totals, average, progress,
daily aggregation), chart PNG output, and web routes (upload errors, non-CSV
rejection, report redirect without upload, full upload-to-report flow,
privacy hidden by default, privacy reveal, skipped-rows listing, PDF and HTML
downloads).

Manual test: uploaded `static/sample_donations.csv` in a browser, verified the
summary numbers, chart, privacy toggle, and both downloads.

## Limitations

- Uploaded data lives in server memory, keyed by session. It is lost on
  restart and not shared between workers; use one gunicorn worker or sticky
  sessions in production.
- 5 MB upload cap.
- Currency is not labeled; amounts are plain numbers.
- Dates outside the accepted formats are rejected rather than guessed.
- No authentication: anyone with the URL can upload and view reports.
- No database: each upload is a fresh, independent report.

## Deploy

Configured for Render via `render.yaml` (gunicorn). Set `SECRET_KEY` and
`TARGET_AMOUNT` as environment variables on the hosting service.
