# -*- coding: utf-8 -*-
"""
TechAbout Foundation: Donation Report Generator.

A small Flask tool. Upload a CSV of contributions, get a summary report with
totals, progress toward a target, a per-day chart, and an optional privacy
mode that hides donor names and amounts. Reports download as PDF or HTML.

The app is fully stateless: parsed data travels in hidden form fields between
requests, so it runs on serverless platforms (Vercel) as well as normal
servers. Nothing is stored on the server.

CSV format (header row required):
    name,amount,date
    Ahmed Khan,5000,2026-09-15
    Sara Malik,2500,2026-09-16

Run locally:
    pip install -r requirements.txt
    python app.py
"""
import csv
import io
import os
import json
import base64
import hashlib
from datetime import datetime, date

import matplotlib
matplotlib.use("Agg")  # headless rendering, no display needed
import matplotlib.pyplot as plt

from flask import (
    Flask, request, render_template,
    send_file, abort,
)
from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload cap

DEFAULT_TARGET = float(os.environ.get("TARGET_AMOUNT", "100000"))

REQUIRED_COLUMNS = ["name", "amount", "date"]
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")


# ---------------------------------------------------------------------------
# CSV parsing and validation
# ---------------------------------------------------------------------------
def _normalize_header(value):
    return value.strip().lower()


def _parse_date(raw):
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(file_stream):
    """Parse an uploaded CSV.

    Returns (donations, errors).
    donations: list of {"name": str, "amount": float, "date": date}
    errors: list of {"row": int, "message": str} for rows that failed validation.
    """
    donations = []
    errors = []

    try:
        text = file_stream.read().decode("utf-8-sig")
    except Exception:
        return [], [{"row": 0, "message": "Could not read the file as UTF-8 text."}]

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], [{"row": 0, "message": "The file is empty."}]

    normalized = [_normalize_header(h) for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in normalized]
    if missing:
        return [], [{
            "row": 0,
            "message": "Missing required column(s): %s. Required columns are: %s."
                       % (", ".join(missing), ", ".join(REQUIRED_COLUMNS)),
        }]

    idx = {col: normalized.index(col) for col in REQUIRED_COLUMNS}

    for row_num, row in enumerate(reader, start=2):  # row 1 is the header
        if not row or all(not cell.strip() for cell in row):
            continue  # skip blank lines silently
        if len(row) < len(header):
            errors.append({"row": row_num,
                           "message": "Row has fewer cells than the header; skipped."})
            continue

        name = row[idx["name"]].strip()
        amount_raw = row[idx["amount"]].strip()
        date_raw = row[idx["date"]].strip()

        if not name:
            errors.append({"row": row_num, "message": "Name is empty; skipped."})
            continue
        try:
            amount = float(amount_raw.replace(",", ""))
        except ValueError:
            errors.append({"row": row_num,
                           "message": "Amount '%s' is not a number; skipped." % amount_raw})
            continue
        if amount <= 0:
            errors.append({"row": row_num,
                           "message": "Amount %s is not positive; skipped." % amount_raw})
            continue
        parsed_date = _parse_date(date_raw)
        if parsed_date is None:
            errors.append({"row": row_num,
                           "message": "Date '%s' is not in a recognized format "
                                      "(use YYYY-MM-DD); skipped." % date_raw})
            continue

        donations.append({"name": name, "amount": amount, "date": parsed_date})

    return donations, errors


# ---------------------------------------------------------------------------
# Payload encode/decode (stateless transport between requests)
#
# The payload carries donor names and amounts between requests in hidden form
# fields. It is encrypted with Fernet (key derived from SECRET_KEY) so names
# and amounts are not visible even in the page source, and tampering is
# rejected.
# ---------------------------------------------------------------------------
def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(
        app.secret_key.encode("utf-8")).digest())
    return Fernet(key)


def encode_payload(donations, errors, target):
    """Serialize and encrypt report data for hidden form fields."""
    raw = json.dumps({
        "donations": [
            {"name": d["name"], "amount": d["amount"],
             "date": d["date"].isoformat()}
            for d in donations
        ],
        "errors": errors,
        "target": target,
    }).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decode_payload(token):
    """Decrypt and restore report data from a posted token."""
    try:
        data = json.loads(_fernet().decrypt(token.encode("ascii")))
    except InvalidToken:
        raise ValueError("Invalid payload")
    donations = [
        {"name": d["name"], "amount": float(d["amount"]),
         "date": date.fromisoformat(d["date"])}
        for d in data["donations"]
    ]
    return donations, data.get("errors", []), float(data.get("target", DEFAULT_TARGET))


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def compute_stats(donations, target):
    """Aggregate totals, averages, progress, and per-day sums."""
    total = sum(d["amount"] for d in donations)
    count = len(donations)
    average = total / count if count else 0.0
    progress = (total / target * 100.0) if target > 0 else 0.0

    daily = {}
    for d in donations:
        key = d["date"].isoformat()
        daily[key] = daily.get(key, 0.0) + d["amount"]
    daily = dict(sorted(daily.items()))

    return {
        "total": total,
        "count": count,
        "average": average,
        "target": target,
        "progress": progress,
        "daily": daily,
    }


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------
def make_chart_png(daily):
    """Bar chart of contributions per day. Returns raw PNG bytes."""
    fig, ax = plt.subplots(figsize=(8, 3.5))
    if daily:
        labels = list(daily.keys())
        values = list(daily.values())
        ax.bar(labels, values)
        ax.set_xlabel("Date")
        ax.set_ylabel("Amount")
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
    ax.set_title("Contributions per day")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def chart_base64(png_bytes):
    return base64.b64encode(png_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------
def build_pdf(stats, donations, show_details, chart_png):
    """Build the PDF report. Returns bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, Image as RLImage)
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=18 * mm, bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("TechAbout Foundation: Donation Report", styles["Title"]))
    story.append(Paragraph("Generated %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
                           styles["Normal"]))
    story.append(Spacer(1, 8 * mm))

    summary_data = [
        ["Total collected", "%.2f" % stats["total"]],
        ["Number of contributors", str(stats["count"])],
        ["Average contribution", "%.2f" % stats["average"]],
        ["Target", "%.2f" % stats["target"]],
        ["Progress", "%.1f%%" % stats["progress"]],
    ]
    table = Table(summary_data, colWidths=[70 * mm, 70 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 8 * mm))

    story.append(RLImage(io.BytesIO(chart_png), width=150 * mm, height=66 * mm))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Contributors", styles["Heading2"]))
    if show_details:
        rows = [["Name", "Amount", "Date"]]
        rows += [[d["name"], "%.2f" % d["amount"], d["date"].isoformat()]
                 for d in donations]
    else:
        rows = [["Donor", "Amount", "Date"]]
        rows += [["Donor %d" % (i + 1), "Hidden", d["date"].isoformat()]
                 for i, d in enumerate(donations)]
    donor_table = Table(rows, colWidths=[60 * mm, 40 * mm, 40 * mm])
    donor_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(donor_table)

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Report rendering helper
# ---------------------------------------------------------------------------
def render_report(donations, errors, target, show_details, standalone=False):
    stats = compute_stats(donations, target)
    png = make_chart_png(stats["daily"])
    return render_template(
        "report.html",
        stats=stats,
        donations=donations,
        errors=errors,
        show_details=show_details,
        chart_b64=chart_base64(png),
        payload=encode_payload(donations, errors, target),
        standalone=standalone,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", default_target=int(DEFAULT_TARGET))


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return render_template("index.html", default_target=int(DEFAULT_TARGET),
                               upload_error="Please choose a CSV file to upload.")
    if not file.filename.lower().endswith(".csv"):
        return render_template("index.html", default_target=int(DEFAULT_TARGET),
                               upload_error="Only .csv files are accepted.")

    try:
        target = float(request.form.get("target", DEFAULT_TARGET))
        if target <= 0:
            raise ValueError
    except ValueError:
        target = DEFAULT_TARGET

    donations, errors = parse_csv(file.stream)
    if not donations and errors:
        return render_template("index.html", default_target=int(DEFAULT_TARGET),
                               upload_error=None, errors=errors)

    # Privacy defaults to hidden; render the report directly (stateless).
    return render_report(donations, errors, target, show_details=False)


@app.route("/report", methods=["GET", "POST"])
def report():
    if request.method == "GET":
        # No server-side state: a bare GET has nothing to show.
        from flask import redirect, url_for
        return redirect(url_for("index"))
    payload = request.form.get("payload", "")
    try:
        donations, errors, target = decode_payload(payload)
    except (ValueError, KeyError):
        abort(400)
    show_details = request.form.get("details") == "shown"
    return render_report(donations, errors, target, show_details)


@app.route("/download", methods=["POST"])
def download():
    payload = request.form.get("payload", "")
    try:
        donations, errors, target = decode_payload(payload)
    except (ValueError, KeyError):
        abort(400)
    fmt = request.form.get("format", "pdf")
    show_details = request.form.get("details") == "shown"

    if fmt == "html":
        html = render_report(donations, errors, target, show_details,
                             standalone=True)
        return send_file(io.BytesIO(html.encode("utf-8")),
                         mimetype="text/html", as_attachment=True,
                         download_name="donation-report.html")

    if fmt == "pdf":
        stats = compute_stats(donations, target)
        png = make_chart_png(stats["daily"])
        pdf_bytes = build_pdf(stats, donations, show_details, png)
        return send_file(io.BytesIO(pdf_bytes),
                         mimetype="application/pdf", as_attachment=True,
                         download_name="donation-report.pdf")

    abort(400)


# Vercel serverless entrypoint: the Python runtime picks up `app`.
# Local dev entrypoint:
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
