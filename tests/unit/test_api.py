import base64
import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from dqflow import source_api
from dqflow.demo import generate, file_hash


def test_paginated_snapshot_exact_bytes(cfg, monkeypatch, tmp_path):
    manifest = generate(cfg, date(2026, 9, 30), 200)
    monkeypatch.setattr(source_api, "root", Path(cfg.source.directory))
    client = TestClient(source_api.app)
    assert client.get("/v1/snapshots?period=2026-09-30").json()["snapshot_id"] == manifest["snapshot_id"]
    for table in manifest["tables"]:
        cursor = ""
        output = bytearray()
        rows = 0
        while cursor is not None:
            page = client.get(
                f"/v1/snapshots/{manifest['snapshot_id']}/{table}", params={"cursor": cursor, "limit": 7}
            ).json()
            output.extend(base64.b64decode(page["csv_base64"]))
            rows += page["row_count"]
            cursor = page["next_cursor"]
        path = tmp_path / f"{table}.csv"
        path.write_bytes(output)
        assert file_hash(path) == manifest["tables"][table]["sha256"]
        assert rows == manifest["tables"][table]["row_count"]


def test_fault_counter_applies_only_to_target_table(cfg, monkeypatch):
    manifest = generate(cfg, date(2026, 9, 30), 20)
    root = Path(cfg.source.directory)
    monkeypatch.setattr(source_api, "root", root)
    monkeypatch.setattr(source_api, "calls", {})
    (root / "fault.json").write_text(
        json.dumps({"id": "retry-test", "table": "orders", "status": 429, "times": 1, "retry_after": 3})
    )
    client = TestClient(source_api.app)
    base = f"/v1/snapshots/{manifest['snapshot_id']}"
    assert client.get(base + "/customers").status_code == 200
    throttled = client.get(base + "/orders")
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"] == "3"
    assert client.get(base + "/orders").status_code == 200
