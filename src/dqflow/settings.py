import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    type: Literal["csv", "http"] = "csv"
    directory: str = "data/demo"
    base_url: str = "http://source-demo:8000"
    token_file: str | None = None
    batch_rows: int = Field(default=5000, ge=1, le=50000)
    attempts: int = Field(default=5, ge=1, le=5)
    connect_timeout: float = 5
    read_timeout: float = 60


class Database(Strict):
    host: str = "localhost"
    port: int = 5432
    database: str = "dqflow"
    password_dir: str = "secrets"
    sslmode: str = "prefer"
    sslrootcert: str | None = None
    statement_timeout_ms: int = 300000
    lock_timeout_ms: int = 10000


class Pipeline(Strict):
    max_runtime_seconds: int = 1800
    source_ready_local: str = "04:30"
    start_local: str = "05:00"
    report_deadline_local: str = "06:00"
    automatic_catchup_days: int = Field(default=7, ge=1, le=31)
    weekdays: list[int] = list(range(7))
    holidays: list[date] = []
    memory_limit_mb: int = 2048
    raw_retention_days: int = 30
    candidate_retention_days: int = 7
    report_retention_days: int = 90
    metadata_retention_days: int = 365


class Artifacts(Strict):
    root: str = "data/artifacts"
    raw: str = "data/raw"


class Notifications(Strict):
    endpoint: str | None = None
    token_file: str | None = None
    attempts: int = 5


class Settings(Strict):
    environment: Literal["local", "test", "production"] = "local"
    dataset: str = Field(default="orders", pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    timezone: str = "Asia/Qyzylorda"
    currency: str = Field(default="KZT", pattern=r"^[A-Z]{3}$")
    source: Source = Source()
    database: Database = Database()
    pipeline: Pipeline = Pipeline()
    artifacts: Artifacts = Artifacts()
    notifications: Notifications = Notifications()
    rules_file: str = "config/rules.yaml"

    @model_validator(mode="after")
    def validate_environment(self):
        ZoneInfo(self.timezone)
        for value in (
            self.pipeline.start_local,
            self.pipeline.source_ready_local,
            self.pipeline.report_deadline_local,
        ):
            time.fromisoformat(value)
        if self.environment == "production":
            if self.source.type == "http" and not self.source.base_url.startswith("https://"):
                raise ValueError("Production source requires HTTPS")
            if self.database.sslmode != "verify-full":
                raise ValueError("Production database requires verify-full TLS")
        return self

    def fingerprint(self):
        rules = Path(self.rules_file).read_bytes()
        return hashlib.sha256(self.model_dump_json().encode() + rules).hexdigest()

    def bounds(self, day: date):
        zone = ZoneInfo(self.timezone)
        return (
            datetime.combine(day, time.min, zone).astimezone(timezone.utc),
            datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc),
        )


def load() -> Settings:
    path = Path(os.environ.get("DQFLOW_CONFIG", "config/local.yaml"))
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def iso_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date must be YYYY-MM-DD")
    return parsed
