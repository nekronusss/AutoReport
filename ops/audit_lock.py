"""Read public PyPI advisory metadata for every exact version in uv.lock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import json
import tomllib
import httpx

packages=tomllib.loads(Path("uv.lock").read_text())["package"]
def inspect(package):
    name,version=package["name"],package["version"]
    try:
        response=httpx.get(f"https://pypi.org/pypi/{name}/{version}/json",timeout=15)
        response.raise_for_status()
        return {"name":name,"version":version,"vulnerabilities":response.json().get("vulnerabilities",[])}
    except Exception as exc:
        return {"name":name,"version":version,"error":type(exc).__name__}
with ThreadPoolExecutor(max_workers=8) as pool:
    results=list(pool.map(inspect,[p for p in packages if p["name"]!="dqflow"]))
out={"checked_at":datetime.now(timezone.utc).isoformat(),"source":"Public PyPI version JSON vulnerabilities metadata",
     "packages":results,"errors":sum("error" in r for r in results),
     "affected_packages":sum(bool(r.get("vulnerabilities")) for r in results)}
Path("docs/evidence/pypi-audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in out.items() if k!="packages"}))
for row in results:
    if row.get("vulnerabilities"):
        print(row["name"],row["version"],[v["id"] for v in row["vulnerabilities"]])
