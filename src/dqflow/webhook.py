import json
import os
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Request

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events")
async def receive(request: Request):
    data = await request.json()
    root = Path(os.environ.get("DQFLOW_MOCK_ROOT", "data/mock"))
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "events.sqlite") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,payload TEXT)")
        conn.execute("INSERT OR IGNORE INTO events VALUES (?,?)", (data["event_id"], json.dumps(data)))
    return {"accepted": True, "event_id": data["event_id"]}
