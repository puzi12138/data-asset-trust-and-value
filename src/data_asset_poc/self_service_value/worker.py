"""One bounded measurement subprocess; accepts registry IDs, never client paths."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import threading

from .gateway import (JOB_TIMEOUT, RUN_ID, acquire_lock, canonical, checked_path,
    digest, input_binding, load_json, registered_input, release_lock, save_json)
from ..signed_delivery.security import utcnow
from ..trustworthiness import evaluate_dataset


def execute(workspace: Path, run_id: str) -> int:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("INVALID_SELF_SERVICE_RUN_ID")
    workspace = checked_path(workspace)
    root = checked_path(workspace / "self_service", workspace)
    folder = checked_path(root / "runs" / run_id, root)
    statefile = checked_path(folder / "status.json", root)
    execution = acquire_lock(root / "worker.lock")
    if execution is None:
        save_json(statefile, {"run_id": run_id, "status": "FAILED", "finished_at": utcnow(),
            "error": {"code": "BUSY", "message": "另一个测量子进程正在运行。"}})
        return 2

    def fail(code, message):
        save_json(statefile, {"run_id": run_id, "status": "FAILED", "finished_at": utcnow(),
            "error": {"code": code, "message": message}})

    def timeout():
        try:
            fail("TIMEOUT", "执行超过120秒，测量已终止。")
        finally:
            os._exit(124)

    timer = threading.Timer(JOB_TIMEOUT, timeout)
    timer.daemon = True
    timer.start()
    try:
        job = load_json(checked_path(folder / "request.json", root))
        protocol = job["protocol"]
        if job.get("run_id") != run_id or digest(canonical(protocol)) != job["request_hash"]:
            raise ValueError("RUN_REQUEST_HASH_CHANGED")
        if load_json(statefile).get("status") != "QUEUED":
            raise ValueError("RUN_NOT_QUEUED")
        save_json(statefile, {"run_id": run_id, "status": "RUNNING", "started_at": utcnow(),
            "worker_pid": os.getpid(), "request_hash": job["request_hash"]})
        records, tables, raw_inputs = {}, {}, {}
        for role, pinned in protocol["inputs"].items():
            records[role], raw_inputs[role], tables[role] = registered_input(root, pinned["dataset_id"])
        if input_binding(records) != protocol["inputs"]:
            raise ValueError("RUN_INPUT_BINDING_CHANGED_BEFORE_EXECUTION")
        from .engine import run_measurement
        measurement_dir = checked_path(folder / "measurement", root)
        report = run_measurement(tables["candidate"], protocol["task"], measurement_dir,
            buyer=tables.get("buyer"), validation=tables.get("validation"), test=tables.get("test"),
            input_records=protocol["inputs"])
        after = {}
        for role, pinned in protocol["inputs"].items():
            after[role], _, _ = registered_input(root, pinned["dataset_id"])
        if input_binding(after) != protocol["inputs"]:
            raise ValueError("RUN_INPUT_BINDING_CHANGED_DURING_EXECUTION")
        trust = {role: evaluate_dataset(tables[role], dataset_id=row["dataset_id"], version=row["version"],
            raw_bytes=raw_inputs[role]) for role, row in records.items()}
        report.update(run_id=run_id, request_hash=job["request_hash"], request_protocol=protocol,
            registered_inputs=protocol["inputs"], source_authenticity="UNKNOWN", content_authenticity="UNKNOWN",
            formal_factual_status="NOT_VERIFIED", signature_status="NOT_PROVIDED",
            trust={"independent_of_task_value": True, "factual_truth_verified": False,
                "source_authenticity": "UNKNOWN", "content_authenticity": "UNKNOWN", "assessments": trust},
            trust_multiplier_applied=False, market_price="NOT_EVALUATED")
        save_json(checked_path(folder / "report.json", root), report)
        report_hash = digest((folder / "report.json").read_bytes())
        save_json(statefile, {"run_id": run_id, "status": "COMPLETED", "finished_at": utcnow(),
            "request_hash": job["request_hash"], "report_sha256": report_hash,
            "input_hashes_verified_before_and_after": True})
        return 0
    except ValueError as exc:
        fail("MEASUREMENT_REJECTED", str(exc))
        return 2
    except Exception as exc:
        fail("WORKER_ERROR", "本地测量未完成：" + type(exc).__name__)
        return 3
    finally:
        timer.cancel()
        release_lock(execution)


def main():
    parser = argparse.ArgumentParser(description="Run one local Self_service_value registered measurement")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    raise SystemExit(execute(args.workspace, args.run_id))


if __name__ == "__main__":
    main()
