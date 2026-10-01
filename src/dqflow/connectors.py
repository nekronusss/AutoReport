import base64
import csv
import hashlib
import json
import random
import re
import shutil
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from dqflow.contracts import Manifest, TABLES
from dqflow.demo import file_hash


class SourceError(Exception):
    pass


class QualityError(Exception):
    def __init__(self, rule, message):
        super().__init__(message)
        self.rule = rule


class SourceClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.deadline = time.monotonic() + cfg.pipeline.max_runtime_seconds
        token = Path(cfg.source.token_file).read_text().strip() if cfg.source.token_file else None
        self.client = httpx.Client(
            timeout=httpx.Timeout(cfg.source.read_timeout, connect=cfg.source.connect_timeout),
            follow_redirects=False,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )

    def close(self):
        self.client.close()

    def get(self, path):
        # Paths are built locally; never follow server-controlled URLs or redirects.
        url = self.cfg.source.base_url.rstrip("/") + path
        if urlsplit(url).netloc != urlsplit(self.cfg.source.base_url).netloc:
            raise SourceError("Cross-origin pagination rejected")
        for attempt in range(self.cfg.source.attempts):
            if time.monotonic() >= self.deadline:
                raise SourceError("Source deadline exceeded")
            retry_after = 0.0
            try:
                response = self.client.get(url)
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise SourceError("Invalid JSON response") from exc
                if response.status_code not in (408, 429, 500, 502, 503, 504):
                    raise SourceError(f"HTTP {response.status_code}; not retryable")
                header = response.headers.get("Retry-After", "0")
                try:
                    retry_after = float(header)
                except ValueError:
                    try:
                        retry_after = (
                            parsedate_to_datetime(header) - datetime.now(timezone.utc)
                        ).total_seconds()
                    except Exception:
                        retry_after = 0
            except (httpx.TimeoutException, httpx.NetworkError):
                pass
            if attempt + 1 == self.cfg.source.attempts:
                break
            delay = max(retry_after, 2**attempt + random.uniform(0, 0.5))
            if time.monotonic() + delay >= self.deadline:
                raise SourceError("Retry would exceed deadline")
            time.sleep(delay)
        raise SourceError("Source retry limit reached")

    def manifest(self, day, snapshot_id=None):
        try:
            if self.cfg.source.type == "csv":
                root = Path(self.cfg.source.directory)
                sid = snapshot_id or json.loads((root / f"{day}.json").read_text())["snapshot_id"]
                if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sid):
                    raise ValueError("Invalid local snapshot ID")
                data = json.loads((root / sid / "manifest.json").read_text())
            else:
                sid = snapshot_id or self.get(f"/v1/snapshots?period={day}")["snapshot_id"]
                data = self.get(f"/v1/snapshots/{quote(sid, safe='')}/manifest")
            manifest = Manifest.model_validate(data)
            if manifest.snapshot_id != sid:
                raise ValueError("Snapshot ID changed")
            if (manifest.period_start, manifest.period_end) != self.cfg.bounds(day):
                raise ValueError("Manifest does not match requested local day")
            return manifest
        except FileNotFoundError as exc:
            raise SourceError("Expected snapshot is missing (DQ18)") from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise QualityError("DQ03", f"Manifest contract: {exc}") from exc

    def extract(self, manifest, dest):
        if not manifest.complete or set(manifest.tables) != set(TABLES):
            raise QualityError("DQ01", "Incomplete manifest or missing table")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
        for table in TABLES:
            path = dest / f"{table}.csv"
            expected = manifest.tables[table]
            if path.exists():
                if file_hash(path) != expected.sha256:
                    raise QualityError("DQ02", f"Previously stored raw file changed: {table}")
                continue
            temp = path.with_suffix(".partial")
            if self.cfg.source.type == "csv":
                try:
                    with (Path(self.cfg.source.directory) / manifest.snapshot_id / f"{table}.csv").open(
                        "rb"
                    ) as src:
                        with temp.open("wb") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                except FileNotFoundError as exc:
                    raise QualityError("DQ01", f"Missing file: {table}") from exc
            else:
                cursor = ""
                seen = set()
                count = 0
                with temp.open("wb") as handle:
                    while True:
                        page = self.get(
                            f"/v1/snapshots/{manifest.snapshot_id}/{table}?cursor={quote(cursor, safe='')}&limit={self.cfg.source.batch_rows}"
                        )
                        if page.get("snapshot_id") != manifest.snapshot_id:
                            raise QualityError("DQ02", "Snapshot changed during pagination")
                        try:
                            chunk = base64.b64decode(page["csv_base64"], validate=True)
                            if hashlib.sha256(chunk).hexdigest() != page["sha256"]:
                                raise QualityError("DQ02", "Page checksum mismatch")
                            count += page["row_count"]
                            if count > expected.row_count:
                                raise QualityError("DQ02", "Too many API rows")
                            handle.write(chunk)
                            cursor = page["next_cursor"]
                        except (KeyError, ValueError, TypeError) as exc:
                            raise QualityError("DQ03", "Malformed page") from exc
                        if cursor is None:
                            break
                        if not isinstance(cursor, str) or cursor in seen:
                            raise QualityError("DQ02", "Repeated or invalid cursor")
                        seen.add(cursor)
            if file_hash(temp) != expected.sha256:
                raise QualityError("DQ02", f"Checksum mismatch: {table}")
            with temp.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != TABLES[table]:
                    raise QualityError("DQ03", f"Unexpected CSV columns: {table}")
                count = sum(1 for _ in reader)
            if count != expected.row_count:
                raise QualityError("DQ02", f"Row count mismatch: {table}")
            temp.rename(path)
        return dest
