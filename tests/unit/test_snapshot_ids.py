import json
from datetime import date
from pathlib import Path

from dqflow.connectors import SourceClient
from dqflow.demo import generate


def test_csv_accepts_contract_snapshot_id_with_dash_and_underscore(cfg):
    day = date(2026, 9, 30)
    data = generate(cfg, day, 20)
    root = Path(cfg.source.directory)
    original = root / data["snapshot_id"]
    data["snapshot_id"] = "snapshot-v1_a"
    renamed = root / data["snapshot_id"]
    original.rename(renamed)
    (renamed / "manifest.json").write_text(json.dumps(data))
    (root / f"{day}.json").write_text(json.dumps({"snapshot_id": data["snapshot_id"]}))
    client = SourceClient(cfg)
    try:
        assert client.manifest(day).snapshot_id == "snapshot-v1_a"
    finally:
        client.close()
