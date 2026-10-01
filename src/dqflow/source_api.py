import base64
import hashlib
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from dqflow.contracts import TABLES

app = FastAPI(title="dqflow synthetic source", docs_url=None, redoc_url=None)
root = Path(os.environ.get("DQFLOW_DEMO_ROOT", "data/demo"))
calls: dict[str, int] = {}


def snapshot(sid):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sid):
        raise HTTPException(400)
    path = root / sid
    if not (path / "manifest.json").exists():
        raise HTTPException(404)
    return path


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/snapshots")
def find(period: str):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        raise HTTPException(400)
    path = root / f"{period}.json"
    if not path.exists():
        raise HTTPException(404)
    return json.loads(path.read_text())


@app.get("/v1/snapshots/{sid}/manifest")
def manifest(sid: str):
    return json.loads((snapshot(sid) / "manifest.json").read_text())


@app.get("/v1/snapshots/{sid}/{table}")
def page(sid: str, table: str, cursor: str = "", limit: int = Query(5000, ge=1, le=50000)):
    path = snapshot(sid)
    if table not in TABLES:
        raise HTTPException(404)
    fault_file = root / "fault.json"
    if fault_file.exists():
        fault = json.loads(fault_file.read_text())
        key = fault.get("id", "fault")
        if fault.get("table", table) == table:
            calls[key] = calls.get(key, 0) + 1
        if fault.get("table", table) == table and calls[key] <= fault.get("times", 1):
            return JSONResponse(
                {"error": "injected"},
                status_code=fault.get("status", 503),
                headers={"Retry-After": str(fault.get("retry_after", 0))},
            )
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "Invalid cursor") from exc
    if offset < 0 or offset > (path / f"{table}.csv").stat().st_size:
        raise HTTPException(400)
    with (path / f"{table}.csv").open("rb") as handle:
        handle.seek(offset)
        data = bytearray(handle.readline() if offset == 0 else b"")
        count = 0
        for _ in range(limit):
            line = handle.readline()
            if not line:
                break
            data.extend(line)
            count += 1
        pos = handle.tell()
        more = bool(handle.read(1))
    return dict(
        snapshot_id=sid,
        row_count=count,
        csv_base64=base64.b64encode(data).decode(),
        sha256=hashlib.sha256(data).hexdigest(),
        next_cursor=base64.urlsafe_b64encode(str(pos).encode()).decode() if more else None,
    )
