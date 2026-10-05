# -*- coding: utf-8 -*-
"""Automated tests for the Donation Report Generator (stateless)."""
import io
import re
import html
import pytest

from app import (app, parse_csv, compute_stats, make_chart_png,
                 encode_payload, decode_payload)


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


def extract_payload(page_html):
    m = re.search(r"name=\"payload\" value='(.*?)'>", page_html, re.S)
    assert m, "payload field not found in report page"
    return html.unescape(m.group(1))


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


# --- Payload round-trip ----------------------------------------------------
def test_payload_round_trip():
    donations, errors = parse_csv(csv_file(VALID_CSV))
    raw = encode_payload(donations, errors, 100000)
    d2, e2, t2 = decode_payload(raw)
    assert len(d2) == 3
    assert d2[0]["name"] == "Ahmed Khan"
    assert d2[0]["date"].isoformat() == "2026-09-15"
    assert t2 == 100000


def test_payload_bad_json_rejected(client):
    resp = client.post("/report", data={"payload": "not-a-valid-token"})
    assert resp.status_code == 400


def test_payload_tampering_rejected(client):
    donations, errors = parse_csv(csv_file(VALID_CSV))
    token = encode_payload(donations, errors, 100000)
    tampered = token[:-4] + "AAAA"
    resp = client.post("/report", data={"payload": tampered})
    assert resp.status_code == 400


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


def test_report_get_redirects_to_index(client):
    resp = client.get("/report")
    assert resp.status_code == 302


def test_upload_renders_report_with_privacy_hidden(client):
    resp = _upload(client)
    assert resp.status_code == 200
    assert b"17500.00" in resp.data            # total shown
    assert b"Ahmed Khan" not in resp.data     # names hidden by default
    assert b"Donor 1" in resp.data


def test_privacy_toggle_reveals_names(client):
    payload = extract_payload(_upload(client).data.decode("utf-8"))
    resp = client.post("/report", data={"payload": payload, "details": "shown"})
    assert resp.status_code == 200
    assert b"Ahmed Khan" in resp.data
    assert b"5000.00" in resp.data


def test_skipped_rows_listed(client):
    text = "name,amount,date\nAli,500,2026-09-15\nBad,xyz,2026-09-15\n"
    resp = _upload(client, text=text)
    assert b"Skipped rows (1)" in resp.data


def test_pdf_download(client):
    payload = extract_payload(_upload(client).data.decode("utf-8"))
    resp = client.post("/download", data={"payload": payload, "format": "pdf"})
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:5] == b"%PDF-"


def test_html_download(client):
    payload = extract_payload(_upload(client).data.decode("utf-8"))
    resp = client.post("/download", data={"payload": payload, "format": "html"})
    assert resp.status_code == 200
    assert b"Donation Report" in resp.data


def test_stateless_two_step_flow(client):
    """Upload, then a fresh client (no cookies) can still render and download."""
    payload = extract_payload(_upload(client).data.decode("utf-8"))
    with app.test_client() as fresh:
        resp = fresh.post("/report", data={"payload": payload})
        assert resp.status_code == 200
        assert b"17500.00" in resp.data
        resp = fresh.post("/download",
                          data={"payload": payload, "format": "pdf"})
        assert resp.data[:5] == b"%PDF-"
