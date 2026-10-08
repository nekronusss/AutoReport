from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from dqflow.contracts import parse_row, TABLES
from dqflow.demo import generate, file_hash
from dqflow.connectors import SourceClient, SourceError, QualityError
from dqflow.reporting import safe_cell
from dqflow.settings import Settings, iso_date


def test_strict_config():
    with pytest.raises(ValidationError):
        Settings.model_validate({"typo": 1})
    with pytest.raises(ValueError):
        iso_date("20260930")


def test_local_day(cfg):
    start, end = cfg.bounds(date(2026, 9, 30))
    assert start.isoformat() == "2026-09-29T19:00:00+00:00"
    assert end - start == timedelta(days=1)


def test_money_and_offsets():
    row = dict(
        order_id="1",
        customer_id="1",
        created_at="2026-09-30T00:00:00+05:00",
        updated_at="2026-09-30T00:01:00+05:00",
        status="completed",
        currency="KZT",
        total_amount="0.10",
    )
    assert parse_row("orders", row)["total_amount"] == Decimal("0.10")
    for amount in ["NaN", "Infinity", "1.001", "1e2", "10000000000000000.00"]:
        with pytest.raises(ValueError):
            parse_row("orders", {**row, "total_amount": amount})
    with pytest.raises(ValueError):
        parse_row("orders", {**row, "created_at": "2026-09-30T00:00:00"})
    with pytest.raises(ValueError):
        parse_row("orders", {**row, "new_column": "x"})


def test_deterministic_manifest_and_counts(cfg):
    day = date(2026, 9, 30)
    a = generate(cfg, day, 200, 42)
    b = generate(cfg, day, 200, 42)
    assert a == b
    assert [a["tables"][t]["row_count"] for t in TABLES] == [10, 50, 90, 50]
    for t in TABLES:
        path = Path(cfg.source.directory) / a["snapshot_id"] / f"{t}.csv"
        assert file_hash(path) == a["tables"][t]["sha256"]


def test_transport_corruption(cfg, tmp_path):
    generate(cfg, date(2026, 9, 30), 200, scenario="corrupt")
    client = SourceClient(cfg)
    with pytest.raises(QualityError, match="Checksum"):
        client.extract(client.manifest(date(2026, 9, 30)), tmp_path / "saved")
    client.close()


def test_http_401_never_retries(cfg):
    count = []

    def respond(request):
        count.append(request)
        return httpx.Response(401)

    client = SourceClient(cfg)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(SourceError, match="401"):
        client.get("/test")
    assert len(count) == 1
    client.close()


def test_http_429_and_deadline(cfg, monkeypatch):
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={"ok": True})]
    )
    delays = []
    client = SourceClient(cfg)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(lambda request: next(responses)))
    monkeypatch.setattr("dqflow.connectors.time.sleep", delays.append)
    assert client.get("/test") == {"ok": True}
    assert delays == [3.0]
    client.close()


@pytest.mark.parametrize("text", ["=HYPERLINK(1)", "+sum(1)", "-1+2", "@cmd", "  =2"])
def test_csv_injection(text):
    assert safe_cell(text).startswith("'")


def test_redirect_not_followed(cfg):
    count = []

    def respond(request):
        count.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.invalid"})

    client = SourceClient(cfg)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)
    with pytest.raises(SourceError, match="302"):
        client.get("/test")
    assert len(count) == 1
    client.close()
