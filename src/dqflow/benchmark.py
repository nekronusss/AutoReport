import math
import platform
import threading
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

import psutil
from dqflow.db import connect
from dqflow.demo import generate
from dqflow.pipeline import run
from dqflow.settings import dumps


def benchmark(cfg, rows=200000, repeat=20, baseline=False):
    if cfg.environment == "production" or repeat < 1:
        raise ValueError("Benchmark requires local/test and positive repeat count")
    day = date(2026, 9, 30)
    manifest = generate(cfg, day, rows, 42)
    results = []
    ident = uuid4().hex[:8]
    for i in range(repeat):
        sample = cfg.model_copy(deep=True)
        # Each sample has a distinct dataset/config identity and executes extraction + COPY + all gates.
        sample.dataset = f"benchmark_{ident}_{i}"
        process = psutil.Process()
        peak = [0]
        stop = threading.Event()

        def memory():
            while not stop.wait(0.05):
                processes = [process] + process.children(recursive=True)
                total = 0
                for proc in processes:
                    try:
                        total += proc.memory_info().rss
                    except psutil.Error:
                        pass
                peak[0] = max(peak[0], total)

        monitor = threading.Thread(target=memory, daemon=True)
        monitor.start()
        started = time.perf_counter()
        result = run(sample, day, baseline=baseline)
        elapsed = time.perf_counter() - started
        stop.set()
        monitor.join()
        with connect(cfg) as conn:
            details = conn.execute(
                "SELECT quality_ms FROM meta.runs WHERE run_id=%s", (result["run_id"],)
            ).fetchone()
        results.append(
            {
                **result,
                "seconds": elapsed,
                "quality_seconds": details["quality_ms"] / 1000 if details["quality_ms"] else None,
                "peak_process_tree_mb": peak[0] / 1024**2,
                "index": i,
                "first_run": i == 0,
            }
        )
        if result["exit_code"]:
            break
    times = sorted(r["seconds"] for r in results)
    quality = sorted(r["quality_seconds"] for r in results if r["quality_seconds"] is not None)

    def percentile(values):
        return values[max(0, math.ceil(0.95 * len(values)) - 1)] if values else None

    output = {
        "rows": rows,
        "seed": 42,
        "snapshot_id": manifest["snapshot_id"],
        "repeat_requested": repeat,
        "baseline_single_inserts": baseline,
        "platform": platform.platform(),
        "cpu_logical": psutil.cpu_count(),
        "ram_bytes": psutil.virtual_memory().total,
        "method": "nearest rank ceil(0.95*n), includes first process run; OS/DB caches not purged",
        "p95_seconds": percentile(times),
        "p95_quality_seconds": percentile(quality),
        "max_process_tree_mb": max(r["peak_process_tree_mb"] for r in results),
        "samples": results,
        "acceptance_hardware": False,
        "exit_code": next((r["exit_code"] for r in results if r["exit_code"]), 0),
    }
    path = Path(cfg.artifacts.root) / f"benchmark-{ident}.json"
    path.write_text(dumps(output), encoding="utf-8")
    return {"path": str(path), **{k: v for k, v in output.items() if k != "samples"}}
