"""Local, hash-bound self-service measurements added to the frozen workbench.

Only registered bytes inside ``workspace/self_service`` are accepted as inputs.
The existing workbench and its local request middleware are retained verbatim.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Literal
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictStr, conint, validator

from ..blind_app.gateway import create_workbench_gateway
from ..dataset_app.gateway import MAX_BYTES, read_csv
from ..signed_delivery.security import utcnow
from ..trustworthiness import evaluate_dataset

MAX_ROWS, MAX_COLUMNS, JOB_TIMEOUT = 10000, 64, 120
DATASET_ID = re.compile(r"^ds_[0-9a-f]{64}$")
RUN_ID = re.compile(r"^run_[0-9a-f]{32}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
PROTOCOL_VERSION = "self_service_value-self-service-api-v1"
TASK_DEFAULTS = {"seed": 101, "model_seeds": [101, 202, 303], "n_bootstrap": 500, "alpha": .05}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(document) -> bytes:
    return json.dumps(document, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def checked_path(path: Path, root: Path | None = None) -> Path:
    """Reject symlinks and Windows reparse points, including parent components."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("SELF_SERVICE_SYMLINK_FORBIDDEN")
        if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("SELF_SERVICE_REPARSE_POINT_FORBIDDEN")
    if root is not None and not path.is_relative_to(Path(os.path.abspath(root))):
        raise ValueError("SELF_SERVICE_PATH_BOUNDARY_FAILED")
    return path


def shared_read_bytes(path: Path) -> bytes:
    checked_path(path)
    # Windows' ordinary open() denies delete sharing, which can briefly block
    # atomic status replacement while a polling request reads the old version.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = create(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read()
    else:
        raw = path.read_bytes()
    return raw


def load_json(path: Path):
    return parse_json(shared_read_bytes(path))


def parse_json(raw: bytes):
    def unique(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("SELF_SERVICE_DUPLICATE_JSON_KEY")
            document[key] = value
        return document
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("SELF_SERVICE_NONFINITE_JSON")))


def save_json(path: Path, document):
    checked_path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    checked_path(temporary)
    with temporary.open("xb") as handle:
        handle.write(canonical(document))
    # A polling stat()/lstat() can still briefly deny replacement on Windows.
    # Root reviewed this separate cause after shared JSON reads alone failed.
    # Exactly five attempts, only PermissionError; never truncate the old file.
    delays = (.01, .02, .04, .08)
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise PermissionError("SELF_SERVICE_ATOMIC_REPLACE_FAILED_TEMP_PRESERVED: " + str(temporary))
            time.sleep(delays[attempt])


def acquire_lock(path: Path):
    """A nonblocking OS lock also coordinates separate gateway instances."""
    checked_path(path)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def release_lock(handle):
    if handle is not None:
        handle.close()


def bounded_frame(raw: bytes, separator: str = ","):
    frame = read_csv(raw, separator)
    if len(frame) > MAX_ROWS:
        raise ValueError("SELF_SERVICE_CSV_ROW_LIMIT_10000")
    if len(frame.columns) > MAX_COLUMNS:
        raise ValueError("SELF_SERVICE_CSV_COLUMN_LIMIT_64")
    return frame


def registered_input(root: Path, identity: str):
    if not DATASET_ID.fullmatch(identity):
        raise ValueError("INVALID_SELF_SERVICE_DATASET_ID")
    folder = checked_path(root / "datasets" / identity, root)
    try:
        metadata = load_json(checked_path(folder / "metadata.json", root))
    except FileNotFoundError as exc:
        if folder.exists():
            raise ValueError("INCOMPLETE_DATASET_REGISTRATION_REQUIRES_REVIEW") from exc
        raise
    datafile = checked_path(folder / "data.csv", root)
    with datafile.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    expected = identity[3:]
    if (len(raw) > MAX_BYTES or digest(raw) != expected or metadata.get("content_hash") != expected
        or metadata.get("dataset_id") != identity or metadata.get("version") != "sha256:" + expected):
        raise ValueError("REGISTERED_INPUT_HASH_OR_VERSION_CHANGED")
    frame = bounded_frame(raw, metadata["separator"])
    schema = [{"name": col, "dtype": str(frame[col].dtype)} for col in frame]
    if (metadata.get("row_count") != len(frame) or metadata.get("column_count") != len(frame.columns)
        or metadata.get("columns") != schema):
        raise ValueError("REGISTERED_INPUT_METADATA_CHANGED")
    return metadata, raw, frame


def input_binding(records: dict) -> dict:
    return {role: {key: row[key] for key in ("dataset_id", "version", "content_hash", "separator",
        "row_count", "column_count", "columns")} for role, row in records.items()}


class TaskInput(BaseModel):
    target: StrictStr | None = None
    task_type: Literal["classification", "regression"] | None = None
    positive_label: StrictStr | None = None
    buyer_mode: Literal["SIMULATED_BUYER_SCENARIO", "REAL_BUYER"] = "SIMULATED_BUYER_SCENARIO"
    split_mode: Literal["record", "entity", "time"] = "record"
    entity_column: StrictStr | None = None
    time_column: StrictStr | None = None
    id_columns: list[StrictStr] = Field(default_factory=list, max_items=64)
    seed: conint(strict=True, ge=0, le=4294967294) = 101
    model_seeds: list[conint(strict=True, ge=0, le=4294967295)] = Field(default_factory=lambda: [101, 202, 303], min_items=1, max_items=3)
    n_bootstrap: conint(strict=True, ge=200, le=2000) = 500
    alpha: float = .05
    target_unit: StrictStr = "target_units"

    @validator("alpha", pre=True)
    def valid_alpha(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value < 1:
            raise ValueError("ALPHA_MUST_BE_FINITE_IN_0_1")
        return value

    @validator("target", "positive_label", "entity_column", "time_column", "target_unit")
    def bounded_text(cls, value):
        if value is not None and (not value.strip() or len(value) > 200):
            raise ValueError("NONEMPTY_TEXT_MAX_200_CHARACTERS")
        return value

    @validator("model_seeds", "id_columns")
    def distinct_values(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("VALUES_MUST_BE_DISTINCT")
        if any(isinstance(v, str) and (not v.strip() or len(v) > 200) for v in values):
            raise ValueError("INVALID_COLUMN_NAME")
        return values

    class Config:
        extra = "forbid"


class RunInput(BaseModel):
    candidate_id: StrictStr
    expected_candidate_hash: StrictStr
    buyer_id: StrictStr | None = None
    validation_id: StrictStr | None = None
    test_id: StrictStr | None = None
    task: TaskInput = Field(default_factory=TaskInput)

    class Config:
        extra = "forbid"


def chinese_markdown(document: dict) -> str:
    return ("# 自助数据集任务价值测量报告\n\n"
        f"- 运行编号：{document['run_id']}\n- 任务协议哈希：{document['request_hash']}\n"
        f"- 任务测量状态：{document.get('status', 'UNKNOWN')}\n"
        "- 来源真实性：UNKNOWN；内容真实性：UNKNOWN；正式事实核验：NOT_VERIFIED。\n"
        "- 可信结构检查与任务价值分别报告；不将可信分乘效用，不估计市场价格。\n"
        "- 模拟买方是研究场景；验证集决定建议，测试集仅事后评价。\n\n"
        "## 完整结果、输入版本与方法\n\n```json\n"
        + json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n```\n")


def start_monitor(target, arguments, name):
    thread = threading.Thread(target=target, args=arguments, daemon=True, name=name)
    thread.start()


def create_self_service_gateway(config_file: Path | str):
    cfg_path = checked_path(Path(config_file))
    cfg = load_json(cfg_path)
    workspace = checked_path(Path(cfg["workspace"]))
    if not workspace.is_dir():
        raise ValueError("SELF_SERVICE_WORKSPACE_MISSING")
    root = checked_path(workspace / "self_service", workspace)
    root.mkdir(exist_ok=True)
    for name in ("datasets", "runs"):
        checked_path(root / name, root).mkdir(exist_ok=True)
    app = create_workbench_gateway(cfg_path)
    app.title = "Self_service_value local self-service dataset measurement"
    app.state.self_service_root = root
    mutex = threading.Lock()

    def public_dataset(row):
        return {key: row[key] for key in ("dataset_id", "version", "content_hash", "row_count",
            "column_count", "columns", "trust", "original_name")}

    def datasets():
        result = []
        checked_path(root / "datasets", root)
        for folder in sorted((root / "datasets").iterdir()):
            checked_path(folder, root)
            if folder.is_dir() and folder.name.startswith("_pending_"):
                continue  # Preserved failed uploads never become public datasets.
            if not folder.is_dir() or not DATASET_ID.fullmatch(folder.name):
                raise ValueError("UNRECOGNIZED_SELF_SERVICE_DATASET_ENTRY")
            row, _, _ = registered_input(root, folder.name)
            result.append(public_dataset(row))
        return result

    def run_file(identity: str, name="status.json"):
        if not RUN_ID.fullmatch(identity):
            raise HTTPException(404, "SELF_SERVICE_RUN_NOT_FOUND")
        path = checked_path(root / "runs" / identity / name, root)
        if not path.is_file():
            raise HTTPException(404, "SELF_SERVICE_RUN_NOT_FOUND")
        return path

    def read_run(identity):
        state = load_json(run_file(identity))
        public = {key: state[key] for key in ("run_id", "status", "error") if key in state}
        if state["status"] == "COMPLETED":
            report_path = run_file(identity, "report.json")
            raw = shared_read_bytes(report_path)
            if digest(raw) != state.get("report_sha256"):
                raise HTTPException(409, "SELF_SERVICE_REPORT_HASH_CHANGED")
            public["report"] = parse_json(raw)
        return public

    def recover_interrupted():
        for folder in (root / "runs").iterdir():
            checked_path(folder, root)
            if not folder.is_dir() or not RUN_ID.fullmatch(folder.name):
                continue
            statefile = checked_path(folder / "status.json", root)
            if statefile.is_file():
                state = load_json(statefile)
                if state.get("status") in {"QUEUED", "RUNNING"}:
                    save_json(statefile, {"run_id": folder.name, "status": "FAILED", "finished_at": utcnow(),
                        "error": {"code": "INTERRUPTED", "message": "上次本地执行已中断；未生成可用结果。"}})

    def monitor(identity: str, reservation):
        folder = checked_path(root / "runs" / identity, root)
        statefile = folder / "status.json"
        env = os.environ.copy()
        source = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
            env[key] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = None
        try:
            process = subprocess.Popen([sys.executable, "-B", "-m", "data_asset_poc.self_service_value.worker",
                "--workspace", str(workspace), "--run-id", identity], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                code = process.wait(timeout=JOB_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                save_json(statefile, {"run_id": identity, "status": "FAILED", "finished_at": utcnow(),
                    "error": {"code": "TIMEOUT", "message": "执行超过120秒，子进程已终止。"}})
                return
            state = load_json(statefile)
            if state.get("status") not in {"COMPLETED", "FAILED"}:
                save_json(statefile, {"run_id": identity, "status": "FAILED", "finished_at": utcnow(),
                    "error": {"code": "WORKER_EXIT", "message": f"子进程退出，未提交完整结果（退出码{code}）。"}})
        except Exception:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            save_json(statefile, {"run_id": identity, "status": "FAILED", "finished_at": utcnow(),
                "error": {"code": "WORKER_START_OR_STATE_ERROR", "message": "本地执行器未能完成；没有生成通过记录。"}})
        finally:
            release_lock(reservation)

    # Reconcile abandoned jobs only when neither a coordinator nor a worker holds a lock.
    reservation = acquire_lock(root / "active.lock")
    execution = acquire_lock(root / "worker.lock") if reservation is not None else None
    try:
        if reservation is not None and execution is not None:
            recover_interrupted()
    finally:
        release_lock(execution)
        release_lock(reservation)

    @app.get("/api/self-service/options")
    def options():
        return {"limits": {"bytes": MAX_BYTES, "rows": MAX_ROWS, "columns": MAX_COLUMNS,
                    "combined_rows": MAX_ROWS, "active_jobs": 1, "job_timeout_seconds": JOB_TIMEOUT},
            "task_types": ["classification", "regression"],
            "learners": {"classification": "LogisticRegression", "regression": "Ridge"},
            "losses": {"classification": "log_loss", "regression": "mae"},
            "default_protocol": TASK_DEFAULTS, "datasets": datasets()}

    @app.get("/api/self-service/datasets")
    def list_datasets():
        return {"datasets": datasets()}

    @app.post("/api/self-service/datasets")
    async def upload(request: Request, name: str = "dataset.csv", separator: str = ","):
        if not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise HTTPException(422, "INVALID_ORIGINAL_NAME")
        raw = bytearray()
        async for block in request.stream():
            if len(raw) + len(block) > MAX_BYTES:
                raise HTTPException(413, "CSV_SIZE_LIMIT_2_MIB")
            raw.extend(block)
        data = bytes(raw)
        frame = bounded_frame(data, separator)
        content_hash = digest(data)
        identity, version = "ds_" + content_hash, "sha256:" + content_hash
        with mutex:
            registration = acquire_lock(root / "registration.lock")
            if registration is None:
                raise HTTPException(409, "DATASET_REGISTRATION_BUSY")
            try:
                folder = checked_path(root / "datasets" / identity, root)
                if folder.exists():
                    try:
                        row, _, _ = registered_input(root, identity)
                    except ValueError as exc:
                        raise HTTPException(409, str(exc)) from exc
                    if row["separator"] != separator:
                        raise HTTPException(409, "SAME_BYTES_ALREADY_REGISTERED_WITH_DIFFERENT_SEPARATOR")
                    return public_dataset(row)
                trust = evaluate_dataset(frame, dataset_id=identity, version=version, raw_bytes=data)
                pending = checked_path(root / "datasets" / ("_pending_" + uuid.uuid4().hex), root)
                pending.mkdir()
                row = {"dataset_id": identity, "version": version, "content_hash": content_hash,
                    "row_count": len(frame), "column_count": len(frame.columns),
                    "columns": [{"name": c, "dtype": str(frame[c].dtype)} for c in frame],
                    "trust": trust, "original_name": name, "separator": separator, "registered_at": utcnow(),
                    "source_authenticity": "UNKNOWN", "content_authenticity": "UNKNOWN",
                    "signature_status": "NOT_PROVIDED", "formal_factual_status": "NOT_VERIFIED"}
                with checked_path(pending / "data.csv", root).open("xb") as handle:
                    handle.write(data)
                save_json(pending / "metadata.json", row)
                os.rename(pending, folder)
                return public_dataset(row)
            finally:
                release_lock(registration)

    @app.post("/api/self-service/runs", status_code=202)
    def start_run(payload: RunInput):
        if not HASH.fullmatch(payload.expected_candidate_hash):
            raise HTTPException(422, "EXPECTED_CANDIDATE_HASH_MUST_BE_SHA256")
        ids = {role: value for role, value in (("candidate", payload.candidate_id), ("buyer", payload.buyer_id),
            ("validation", payload.validation_id), ("test", payload.test_id)) if value is not None}
        records = {}
        for role, identity in ids.items():
            try:
                records[role], _, _ = registered_input(root, identity)
            except FileNotFoundError:
                raise HTTPException(404, "REGISTERED_DATASET_NOT_FOUND")
        if records["candidate"]["content_hash"] != payload.expected_candidate_hash:
            raise HTTPException(409, "CANDIDATE_HASH_MISMATCH")
        if sum(row["row_count"] for row in records.values()) > MAX_ROWS:
            raise HTTPException(422, "COMBINED_INPUT_ROWS_EXCEED_10000")
        task = payload.task.dict()
        if (payload.validation_id is None) != (payload.test_id is None):
            raise HTTPException(422, "VALIDATION_AND_TEST_MUST_BE_PROVIDED_TOGETHER")
        if task["buyer_mode"] == "SIMULATED_BUYER_SCENARIO" and len(ids) > 1:
            raise HTTPException(422, "SIMULATED_MODE_USES_CANDIDATE_ONLY")
        if task["buyer_mode"] == "REAL_BUYER" and payload.buyer_id is None:
            raise HTTPException(422, "REAL_BUYER_REQUIRES_BUYER_DATASET")
        if task["split_mode"] == "entity" and not task["entity_column"]:
            raise HTTPException(422, "ENTITY_SPLIT_REQUIRES_ENTITY_COLUMN")
        if task["split_mode"] == "time" and not task["time_column"]:
            raise HTTPException(422, "TIME_SPLIT_REQUIRES_TIME_COLUMN")
        with mutex:
            reservation = acquire_lock(root / "active.lock")
            if reservation is None:
                raise HTTPException(409, "BUSY")
            execution = acquire_lock(root / "worker.lock")
            if execution is None:
                release_lock(reservation)
                raise HTTPException(409, "BUSY")
            release_lock(execution)
            try:
                recover_interrupted()
                identity = "run_" + uuid.uuid4().hex
                folder = checked_path(root / "runs" / identity, root)
                folder.mkdir()
                protocol = {"protocol_version": PROTOCOL_VERSION, "inputs": input_binding(records), "task": task,
                    "learner_policy": {"classification": "LogisticRegression_C1_lbfgs", "regression": "Ridge_alpha1_cholesky"},
                    "loss_policy": {"classification": "log_loss", "regression": "mae"},
                    "test_policy": "POSTHOC_ONLY_VALIDATION_DECISION_FROZEN_FIRST"}
                job = {"run_id": identity, "created_at": utcnow(), "protocol": protocol,
                    "request_hash": digest(canonical(protocol))}
                save_json(folder / "request.json", job)
                save_json(folder / "status.json", {"run_id": identity, "status": "QUEUED", "created_at": job["created_at"]})
                start_monitor(monitor, (identity, reservation), "self_service_value-" + identity)
            except Exception:
                release_lock(reservation)
                raise
        return {"run_id": identity, "status": "QUEUED", "poll_url": "/api/self-service/runs/" + identity}

    @app.get("/api/self-service/runs/{run_id}")
    def get_run(run_id: str):
        return read_run(run_id)

    @app.get("/api/self-service/runs/{run_id}/report.json")
    def json_report(run_id: str):
        state = read_run(run_id)
        if state["status"] != "COMPLETED":
            raise HTTPException(409, "SELF_SERVICE_RUN_NOT_COMPLETED")
        return JSONResponse(state["report"], headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'})

    @app.get("/api/self-service/runs/{run_id}/report.md")
    def markdown_report(run_id: str):
        state = read_run(run_id)
        if state["status"] != "COMPLETED":
            raise HTTPException(409, "SELF_SERVICE_RUN_NOT_COMPLETED")
        return Response(chinese_markdown(state["report"]), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'})

    return app
