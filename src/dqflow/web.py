from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
import shutil

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, PlainTextResponse
from jinja2 import Environment, select_autoescape

from dqflow.db import connect
from dqflow.reporting import ARTIFACTS, status
from dqflow.settings import load, iso_date

app = FastAPI(title="dqflow reports", docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    try:
        with connect(load(), "reader") as conn:
            conn.execute("SELECT 1 FROM meta.publications LIMIT 1")
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc


@app.get("/", response_class=HTMLResponse)
def index():
    cfg = load()
    with connect(cfg, "reader") as conn:
        rows = conn.execute(
            """SELECT p.period,p.run_id,r.as_of,r.state,r.code_version,
          EXISTS(SELECT 1 FROM meta.report_artifacts a WHERE a.run_id=p.run_id AND name='summary.html') ready
          FROM meta.publications p JOIN meta.runs r USING(run_id) WHERE p.dataset=%s ORDER BY period DESC""",
            (cfg.dataset,),
        ).fetchall()
        recent = conn.execute(
            "SELECT period,state,created_at,error_code FROM meta.runs WHERE dataset=%s ORDER BY created_at DESC LIMIT 8",
            (cfg.dataset,),
        ).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        row["age_hours"] = round((now - row["as_of"]).total_seconds() / 3600, 1)
    template = Environment(autoescape=select_autoescape(default=True)).from_string(
        """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>dqflow · Публикации</title><style>body{margin:0;background:#f3f5ef;color:#153b3e;font:15px/1.6 system-ui}main{max-width:1050px;margin:50px auto;padding:0 24px}h1{font-size:42px;letter-spacing:-1.5px;font-weight:550}header{font-size:26px;font-weight:800;border-bottom:1px solid #d8e3df;padding-bottom:20px}header span,a{color:#13786e}p,small{color:#617574}table{width:100%;border-collapse:collapse;background:white;border-radius:12px}td,th{padding:14px;text-align:left;border-bottom:1px solid #d8e3df}th{font-size:12px;background:#e9efe8}a{text-decoration:none}h2{margin-top:40px;font-size:22px}.scroll{overflow:auto}.tag{font-size:11px;background:#e6efe4;border-radius:20px;padding:5px 10px}</style><main><header>dq<span>flow</span></header><small>ЕЖЕДНЕВНАЯ ОТЧЁТНОСТЬ / {{ dataset }}</small><h1>Проверенные данные.<br>Прозрачная история.</h1><p>Текущая версия каждого периода. Возраст рассчитан от as_of источника; ссылка всегда соответствует опубликованному run_id.</p><h2>Опубликованные периоды</h2><div class="scroll"><table><tr><th>Период</th><th>Состояние</th><th>Возраст снимка</th><th>Отчёт</th></tr>{% for r in rows %}<tr><td>{{ r.period }}</td><td><span class="tag">{{ r.state }}</span></td><td>{{ r.age_hours }} ч<br><small>{{ r.as_of }}</small></td><td>{% if r.ready %}<a href="/current/{{ r.period }}">Открыть ↗</a>{% else %}Ожидает формирования{% endif %}</td></tr>{% else %}<tr><td colspan="4">Публикаций пока нет. Запустите загрузку через dqflow run.</td></tr>{% endfor %}</table></div><h2>Последние попытки</h2><div class="scroll"><table><tr><th>Период</th><th>Результат</th><th>Время UTC</th><th>Диагностика</th></tr>{% for r in recent %}<tr><td>{{ r.period }}</td><td>{{ r.state }}</td><td>{{ r.created_at }}</td><td>{{ r.error_code or '—' }}</td></tr>{% endfor %}</table></div><p>Синтетический демонстрационный источник · {{ now }}</p></main></html>"""
    )
    return template.render(rows=rows, recent=recent, dataset=cfg.dataset, now=now)


@app.get("/current/{period}")
def current(period: str):
    result = status(load(), iso_date(period))
    if result.get("report_state") != "READY":
        return HTMLResponse("Отчёт текущей публикации ожидает формирования.", status_code=202)
    return RedirectResponse(result["report"], status_code=303)


@app.get("/reports/{run_id}/{name}")
def artifact(run_id: UUID, name: str):
    if name not in ARTIFACTS:
        raise HTTPException(404)
    cfg = load()
    with connect(cfg, "reader") as conn:
        row = conn.execute(
            "SELECT path FROM meta.report_artifacts WHERE run_id=%s AND name=%s", (run_id, name)
        ).fetchone()
    if not row:
        raise HTTPException(404)
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(Path(cfg.artifacts.root).resolve()) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        },
    )


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    cfg = load()
    with connect(cfg, "reader") as conn:
        pub = (
            conn.execute(
                "SELECT extract(epoch FROM max(published_at)) v FROM meta.publications WHERE dataset=%s",
                (cfg.dataset,),
            ).fetchone()["v"]
            or 0
        )
        outbox = conn.execute(
            "SELECT count(*) n,extract(epoch FROM now()-min(created_at)) age FROM meta.outbox WHERE status='PENDING'"
        ).fetchone()
        scheduler = conn.execute(
            "SELECT extract(epoch FROM last_seen) v FROM meta.service_health WHERE name='scheduler'"
        ).fetchone()
        backup = conn.execute(
            "SELECT extract(epoch FROM last_seen) v FROM meta.service_health WHERE name='external_backup'"
        ).fetchone()
        heartbeat = conn.execute(
            "SELECT coalesce(max(extract(epoch FROM now()-heartbeat)),0) v FROM meta.runs WHERE state IN ('EXTRACTING','LOADING','VALIDATING','PUBLISHING')"
        ).fetchone()["v"]
        latest = (
            conn.execute(
                "SELECT extract(epoch FROM max(as_of)) v FROM meta.runs r JOIN meta.publications p USING(run_id) WHERE p.dataset=%s",
                (cfg.dataset,),
            ).fetchone()["v"]
            or 0
        )
    disk = shutil.disk_usage(cfg.artifacts.root)
    values = {
        "dqflow_last_publication_timestamp": pub,
        "dqflow_source_as_of_timestamp": latest,
        "dqflow_outbox_pending": outbox["n"],
        "dqflow_outbox_oldest_seconds": outbox["age"] or 0,
        "dqflow_scheduler_timestamp": scheduler["v"] if scheduler else 0,
        "dqflow_external_backup_timestamp": backup["v"] if backup else 0,
        "dqflow_run_heartbeat_age_seconds": heartbeat,
        "dqflow_disk_used_ratio": 1 - disk.free / disk.total,
    }
    return "\n".join(f"# TYPE {k} gauge\n{k} {v}" for k, v in values.items()) + "\n"
