import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from dqflow.settings import Strict

TABLES = {
    "customers": ["customer_id", "created_at", "updated_at"],
    "orders": ["order_id", "customer_id", "created_at", "updated_at", "status", "currency", "total_amount"],
    "order_items": ["order_id", "line_id", "sku", "quantity", "unit_price", "updated_at"],
    "payments": [
        "payment_id",
        "order_id",
        "kind",
        "status",
        "amount",
        "currency",
        "occurred_at",
        "updated_at",
    ],
}
KEYS = {
    "customers": ["customer_id"],
    "orders": ["order_id"],
    "order_items": ["order_id", "line_id"],
    "payments": ["payment_id"],
}
MONEY = {"orders": "total_amount", "order_items": "unit_price", "payments": "amount"}


def aware(value):
    if value.tzinfo is None:
        raise ValueError("Timestamp requires explicit UTC offset")
    return value


Timestamp = Annotated[datetime, AfterValidator(aware)]


class TableManifest(Strict):
    row_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    money_totals: dict[str, str] = {}


class Manifest(Strict):
    source: str = Field(min_length=1)
    schema_version: Literal["1.0"]
    snapshot_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    period_start: Timestamp
    period_end: Timestamp
    as_of: Timestamp
    complete: bool
    tables: dict[str, TableManifest]

    @model_validator(mode="after")
    def bounds(self):
        if self.period_end <= self.period_start or self.as_of < self.period_end:
            raise ValueError("Invalid snapshot time range")
        return self


def parse_row(table, row):
    """Structural validation only; business rule violations remain in typed input."""
    if set(row) != set(TABLES[table]) or any(v is None for v in row.values()):
        raise ValueError("Unexpected/missing fields or null value")
    result = dict(row)
    for name, value in row.items():
        if not isinstance(value, str):
            raise ValueError(f"{name}: expected string")
        if name.endswith("_at"):
            try:
                result[name] = aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError as exc:
                raise ValueError(f"{name}: invalid timestamp or missing UTC offset") from exc
        elif name in ("total_amount", "amount", "unit_price"):
            if not re.fullmatch(r"-?\d{1,16}(?:\.\d{1,2})?", value):
                raise ValueError(f"{name}: invalid numeric(18,2)")
            result[name] = Decimal(value)
        elif name == "quantity":
            if not re.fullmatch(r"-?\d{1,9}", value):
                raise ValueError("quantity: invalid integer")
            result[name] = int(value)
    return result
