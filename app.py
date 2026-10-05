# -*- coding: utf-8 -*-
"""
TechAbout Foundation: Donation Report Generator.

A small Flask tool. Upload a CSV of contributions, get a summary report with
totals, progress toward a target, a per-day chart, and an optional privacy
mode that hides donor names and amounts. Reports download as PDF or HTML.

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
import base64
import secrets
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # headless rendering, no display needed
import matplotlib.pyplot as plt

from flask import (
    Flask, request, render_template, redirect,
    url_for, session, send_file, abort,
)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload cap

DEFAULT_TARGET = float(os.environ.get("TARGET_AMOUNT", "100000"))

REQUIRED_COLUMNS = ["name", "amount", "date"]
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")

# In-memory store: token -> parsed data. Fine for a small single-worker tool.
# Limitation: data is lost on restart and not shared across workers.
STORE = {}


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
# Routes
# ---------------------------------------------------------------------------
def _get_record():
    token = session.get("report_token")
    if not token:
        return None
    return STORE.get(token)


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
        # Nothing usable at all: show errors on the upload page.
        return render_template("index.html", default_target=int(DEFAULT_TARGET),
                               upload_error=None, errors=errors)

    token = secrets.token_hex(16)
    STORE[token] = {"donations": donations, "errors": errors, "target": target}
    session["report_token"] = token
    return redirect(url_for("report"))


@app.route("/report", methods=["GET"])
def report():
    record = _get_record()
    if record is None:
        return redirect(url_for("index"))
    show_details = request.args.get("details") == "shown"
    stats = compute_stats(record["donations"], record["target"])
    png = make_chart_png(stats["daily"])
    return render_template(
        "report.html",
        stats=stats,
        donations=record["donations"],
        errors=record["errors"],
        show_details=show_details,
        chart_b64=chart_base64(png),
    )


@app.route("/download", methods=["GET"])
def download():
    record = _get_record()
    if record is None:
        return redirect(url_for("index"))
    fmt = request.args.get("format", "pdf")
    show_details = request.args.get("details") == "shown"
    stats = compute_stats(record["donations"], record["target"])
    png = make_chart_png(stats["daily"])

    if fmt == "html":
        html = render_template(
            "report.html", stats=stats, donations=record["donations"],
            errors=record["errors"], show_details=show_details,
            chart_b64=chart_base64(png), standalone=True,
        )
        return send_file(io.BytesIO(html.encode("utf-8")),
                         mimetype="text/html", as_attachment=True,
                         download_name="donation-report.html")

    if fmt == "pdf":
        pdf_bytes = build_pdf(stats, record["donations"], show_details, png)
        return send_file(io.BytesIO(pdf_bytes),
                         mimetype="application/pdf", as_attachment=True,
                         download_name="donation-report.pdf")

    abort(400)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
