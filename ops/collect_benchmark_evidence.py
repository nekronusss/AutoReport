import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from dqflow.db import connect
from dqflow.pipeline import code_hash
from dqflow.settings import load, dumps

parser = argparse.ArgumentParser()
parser.add_argument("input")
parser.add_argument("--output", default="docs/evidence/benchmark-release.json")
args = parser.parse_args()
data = json.loads(Path(args.input).read_text())
ids = [r["run_id"] for r in data["samples"]]
with connect(load()) as conn:
    runs = conn.execute("SELECT run_id,code_version,state,row_count FROM meta.runs WHERE run_id=ANY(%s::uuid[])", (ids,)).fetchall()
    checks = conn.execute("SELECT run_id,count(*) n,bool_and(severity<>'ERROR' OR status='PASS') valid FROM meta.check_results WHERE run_id=ANY(%s::uuid[]) GROUP BY run_id", (ids,)).fetchall()
assert len(runs) == len(ids) == len(checks)
assert all(r["state"] == "SUCCEEDED" and r["row_count"] == data["rows"] for r in runs)
assert all(c["n"] == 20 and c["valid"] for c in checks)
result = {"measured_code_versions": sorted({r["code_version"] for r in runs}),
          "current_code_version": code_hash(),
          "uv_lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
          "versions": {p: version(p) for p in ("dbt-core", "dbt-postgres", "sqlparse", "fastapi", "starlette", "psycopg", "pydantic")},
          "verified_real_runs": len(runs), "quality_results": len(checks) * 20, "all_error_gates_passed": True}
Path(args.output).write_text(dumps(result), encoding="utf-8")
print(dumps(result))
