# -*- coding: utf-8 -*-
"""Automated tests for the Donation Report Generator."""
import io
import pytest

from app import app, parse_csv, compute_stats, make_chart_png


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def csv_file(text):
    return io.BytesIO(text.encode("utf-8"))


VALID_CSV = """name,amount,date
Ahmed Khan,5000,2026-09-15
Sara Malik,2500,2026-09-15
Bilal Ahmed,10000,2026-09-16
"""


# --- CSV parsing -----------------------------------------------------------
def test_valid_csv_parses():
    donations, errors = parse_csv(csv_file(VALID_CSV))
    assert errors == []
    assert len(donations) == 3
    assert donations[0]["name"] == "Ahmed Khan"
    assert donations[0]["amount"] == 5000.0


def test_missing_column_error():
    donations, errors = parse_csv(csv_file("name,date\nAli,2026-09-15\n"))
    assert donations == []
    assert len(errors) == 1
    assert "amount" in errors[0]["message"]


def test_empty_file_error():
    donations, errors = parse_csv(csv_file(""))
    assert donations == []
    assert errors


def test_bad_amount_row_flagged():
    donations, errors = parse_csv(csv_file("name,amount,date\nAli,abc,2026-09-15\n"))
    assert donations == []
    assert len(errors) == 1
    assert errors[0]["row"] == 2


def test_negative_amount_row_flagged():
    donations, errors = parse_csv(csv_file("name,amount,date\nAli,-500,2026-09-15\n"))
    assert donations == []
    assert any("positive" in e["message"] for e in errors)


def test_bad_date_row_flagged():
    donations, errors = parse_csv(csv_file("name,amount,date\nAli,500,not-a-date\n"))
    assert donations == []
    assert len(errors) == 1


def test_empty_name_row_flagged():
    donations, errors = parse_csv(csv_file("name,amount,date\n,500,2026-09-15\n"))
    assert donations == []
    assert any("Name is empty" in e["message"] for e in errors)


def test_good_rows_kept_when_some_bad():
    text = "name,amount,date\nAli,500,2026-09-15\nBad,xyz,2026-09-15\nSara,700,2026-09-16\n"
    donations, errors = parse_csv(csv_file(text))
    assert len(donations) == 2
    assert len(errors) == 1
    assert errors[0]["row"] == 3


def test_alternate_date_format_accepted():
    donations, errors = parse_csv(csv_file("name,amount,date\nAli,500,15/09/2026\n"))
    assert errors == []
    assert donations[0]["date"].isoformat() == "2026-09-15"


# --- Statistics ------------------------------------------------------------
def test_stats_totals():
    donations, _ = parse_csv(csv_file(VALID_CSV))
    stats = compute_stats(donations, 100000)
    assert stats["total"] == 17500.0
    assert stats["count"] == 3
    assert stats["average"] == pytest.approx(5833.33, abs=0.01)


def test_progress_calc():
    donations, _ = parse_csv(csv_file(VALID_CSV))
    stats = compute_stats(donations, 35000)
    assert stats["progress"] == pytest.approx(50.0)


def test_daily_aggregation():
    donations, _ = parse_csv(csv_file(VALID_CSV))
    stats = compute_stats(donations, 100000)
    assert stats["daily"] == {"2026-09-15": 7500.0, "2026-09-16": 10000.0}


def test_chart_returns_png():
    png = make_chart_png({"2026-09-15": 7500.0})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# --- Web routes ------------------------------------------------------------
def _upload(client, text=VALID_CSV, filename="donations.csv"):
    data = {"file": (io.BytesIO(text.encode("utf-8")), filename),
            "target": "100000"}
    return client.post("/upload", data=data,
                       content_type="multipart/form-data")


def test_upload_no_file_shows_error(client):
    resp = client.post("/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"Please choose a CSV file" in resp.data


def test_upload_non_csv_rejected(client):
    data = {"file": (io.BytesIO(b"x"), "notes.txt")}
    resp = client.post("/upload", data=data, content_type="multipart/form-data")
    assert b"Only .csv files are accepted" in resp.data


def test_report_redirects_without_upload(client):
    resp = client.get("/report")
    assert resp.status_code == 302


def test_full_flow_and_privacy_default_hidden(client):
    _upload(client)
    resp = client.get("/report")
    assert resp.status_code == 200
    assert b"17500.00" in resp.data            # total shown
    assert b"Ahmed Khan" not in resp.data      # names hidden by default
    assert b"Donor 1" in resp.data


def test_privacy_shown_with_param(client):
    _upload(client)
    resp = client.get("/report?details=shown")
    assert b"Ahmed Khan" in resp.data
    assert b"5000.00" in resp.data


def test_skipped_rows_listed(client):
    text = "name,amount,date\nAli,500,2026-09-15\nBad,xyz,2026-09-15\n"
    _upload(client, text=text)
    resp = client.get("/report")
    assert b"Skipped rows (1)" in resp.data


def test_pdf_download(client):
    _upload(client)
    resp = client.get("/download?format=pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:5] == b"%PDF-"


def test_html_download(client):
    _upload(client)
    resp = client.get("/download?format=html")
    assert resp.status_code == 200
    assert b"Donation Report" in resp.data
