"""Local isolated restore drill. No external backup claims; never overwrites a database."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

import yaml
from psycopg import sql
from dqflow.db import connect
from dqflow.demo import file_hash
from dqflow.pipeline import run
from dqflow.settings import load, dumps


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--pg-bin",required=True)
    args=parser.parse_args()
    cfg=load()
    if cfg.environment=="production":
        raise SystemExit("Local drill only")
    started=time.perf_counter()
    ident=uuid4().hex[:10]
    root=Path(".tools")/f"restore-{ident}"
    root.mkdir(parents=True)
    dbname=f"dqflow_restore_{ident}"
    common=["-h",cfg.database.host,"-p",str(cfg.database.port),"-U","postgres"]
    suffix=".exe" if os.name=="nt" else ""
    def command(tool,*argv):
        subprocess.run([str(Path(args.pg_bin)/(tool+suffix)),*common,*argv],check=True,capture_output=True)
    dump=root/"database.dump"
    command("pg_dump","-Fc","-f",str(dump),cfg.database.database)
    command("createdb","-O","dqflow_migrator",dbname)
    command("pg_restore","--exit-on-error","-d",dbname,str(dump))
    restored=cfg.model_copy(deep=True)
    restored.database.database=dbname
    restored.environment="test"
    restored.notifications.endpoint=None
    restored.source.type="csv"
    mapping={}
    for name,old in (("raw",cfg.artifacts.raw),("artifacts",cfg.artifacts.root),("demo",cfg.source.directory)):
        dest=root/name
        shutil.copytree(old,dest)
        mapping[Path(old).resolve()]=dest.resolve()
    restored.artifacts.raw=str((root/"raw").resolve())
    restored.artifacts.root=str((root/"artifacts").resolve())
    restored.source.directory=str((root/"demo").resolve())
    with connect(restored,"admin") as conn:
        conn.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO dqflow_runner").format(sql.Identifier(dbname)))
        for table in ("meta.source_batches","meta.report_artifacts"):
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                old=Path(row["path"]).resolve()
                replacement=None
                for original,dest in mapping.items():
                    if old.is_relative_to(original):
                        replacement=dest/old.relative_to(original)
                        break
                if not replacement or not replacement.is_file() or file_hash(replacement)!=row["sha256"]:
                    raise RuntimeError(f"Unrecoverable referenced file in {table}")
                conn.execute(f"UPDATE {table} SET path=%s WHERE run_id=%s AND path=%s",(str(replacement),row["run_id"],row["path"]))
        before=conn.execute("SELECT * FROM mart.current_daily_summary ORDER BY business_date").fetchall()
        current=conn.execute("SELECT period,run_id FROM meta.publications WHERE dataset=%s ORDER BY period DESC LIMIT 1",(cfg.dataset,)).fetchone()
    config=root/"config.yaml"
    config.write_text(yaml.safe_dump(restored.model_dump(mode="json")),encoding="utf-8")
    subprocess.run([str(Path(os.sys.executable).parent/("dqflow"+suffix)),"doctor"],
                   env={**os.environ,"DQFLOW_CONFIG":str(config)},check=True,capture_output=True)
    result=run(restored,current["period"])
    if result["exit_code"]!=0:
        raise RuntimeError(result)
    with connect(restored) as conn:
        after=conn.execute("SELECT * FROM mart.current_daily_summary ORDER BY business_date").fetchall()
    for rows in (before,after):
        for row in rows:
            row.pop("run_id")
    if before!=after:
        raise RuntimeError("Replay totals differ")
    evidence={"status":"PASSED","backup_sha256":file_hash(dump),"isolated_database":dbname,
              "files_verified":True,"replay":result,"summary":after,"seconds":time.perf_counter()-started,
              "external_backup":False,"scope":"local pg_dump/restore, artifact checksums, doctor, replay"}
    Path("docs/evidence").mkdir(exist_ok=True,parents=True)
    Path("docs/evidence/restore.json").write_text(dumps(evidence),encoding="utf-8")
    print(dumps(evidence))


if __name__=="__main__":
    main()
