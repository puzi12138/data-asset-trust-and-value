"""Version-bound local visual evidence registry (Visual_evidence_live).

Local receipts authenticate this configured process's byte/binding checks. They
do not authenticate declared sources, certify facts, or create independent
observations. The optional trusted_verifier is injected by the server operator,
never read from uploaded JSON. Its attestations are narrowly scoped evidence
assertions, and do not change fact_status=UNKNOWN.
"""
from __future__ import annotations

import base64
from collections import Counter
from contextlib import contextmanager
import copy
import csv
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid
import warnings

from PIL import Image


METHOD_VERSION = "visual_evidence_live-local-evidence-registry-v1"
STATES = ("MATCH", "MISMATCH", "INCOMPARABLE", "DISPUTED")
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_COLUMNS = 200
MAX_EVIDENCE = 4
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class RegistryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RegistryError("FINITE_JSON_REQUIRED") from exc


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, name: str, maximum=8192, *, empty=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not value.strip() and not empty):
        raise RegistryError(name + "_INVALID")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise RegistryError(name + "_CONTROL_CHARACTER")
    return value


def _identifier(value, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise RegistryError(name + "_INVALID")
    return value


def _sha(value, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RegistryError(name + "_INVALID")
    return value


def _decode(value, maximum: int, name: str) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise RegistryError(name + "_SIZE_LIMIT")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise RegistryError(name + "_INVALID_BASE64") from exc
    if not raw or len(raw) > maximum:
        raise RegistryError(name + "_SIZE_LIMIT")
    return raw


def _image(raw: bytes, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise RegistryError("PNG_OR_JPEG_REQUIRED")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
                actual_format = im.format
                if actual_format not in {"PNG", "JPEG"}:
                    raise RegistryError("PNG_OR_JPEG_REQUIRED")
                if ((suffix == ".png") != (actual_format == "PNG")):
                    raise RegistryError("IMAGE_EXTENSION_CONTENT_MISMATCH")
                width, height = im.size
                if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                    raise RegistryError("IMAGE_PIXEL_LIMIT")
                if getattr(im, "n_frames", 1) != 1:
                    raise RegistryError("SINGLE_FRAME_IMAGE_REQUIRED")
                im.verify()
            with Image.open(io.BytesIO(raw)) as im:
                # Ignore metadata, including presentation orientation. Rotating
                # an existing image is not a new physical acquisition.
                rgba = im.convert("RGBA")
                pixels = canonical({"width": width, "height": height, "mode": "RGBA"}) + b"\0" + rgba.tobytes()
        return {"sha256": digest(raw), "pixel_sha256": digest(pixels), "width": width,
                "height": height, "media_type": "image/png" if actual_format == "PNG" else "image/jpeg"}
    except RegistryError:
        raise
    except Exception as exc:
        raise RegistryError("IMAGE_DECODE_FAILED") from exc


class Registry:
    """Append-only runtime store; same-user filesystem access is not isolation.

    ``trusted_verifier(context, image_bytes)`` is an optional explicitly
    configured local workflow. If it returns an assertion, it must also return
    the exact proposition hash, verifier_id, source_identity_verified=True and
    relevance_verified=True. Uploaded validation_receipt strings are never used.
    """

    def __init__(self, root: Path, *, trusted_verifier=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._trusted_verifier = trusted_verifier
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(str(self.root), threading.RLock())
        with self._locked():
            private = self.root / ".private"
            private.mkdir(exist_ok=True)
            self._inside(private)
            key = private / "receipt.key"
            if not key.exists():
                if (self.root / "cases").exists() and any((self.root / "cases").iterdir()):
                    raise RegistryError("LOCAL_TRUST_KEY_MISSING")
                fd = os.open(str(key), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            self._inside(key)
            self._key = key.read_bytes()
            if len(self._key) != 32:
                raise RegistryError("LOCAL_TRUST_KEY_INVALID")
            (self.root / "cases").mkdir(exist_ok=True)

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or path.is_symlink():
            raise RegistryError("OUTSIDE_REGISTRY_PATH")
        return resolved

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            lockfile = self.root / ".registry.lock"
            self._inside(lockfile)
            with lockfile.open("a+b") as stream:
                stream.seek(0, 2)
                if not stream.tell():
                    stream.write(b"0")
                    stream.flush()
                end = time.monotonic() + 5
                while True:
                    try:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if time.monotonic() >= end:
                            raise RegistryError("REGISTRY_BUSY") from exc
                        time.sleep(.02)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _mac(self, payload: dict) -> str:
        return hmac.new(self._key, canonical(payload), hashlib.sha256).hexdigest()

    def _write(self, path: Path, payload: dict):
        self._inside(path)
        envelope = {"payload": payload, "local_mac": self._mac(payload)}
        # Every destination name is new. Atomic rename prevents readers seeing
        # partial JSON; locks serialize writers, and old records never change.
        temporary = path.parent / (".pending-" + uuid.uuid4().hex)
        with temporary.open("xb") as stream:
            stream.write(canonical(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise RegistryError("APPEND_ONLY_DESTINATION_EXISTS")
        temporary.rename(path)

    def _read(self, path: Path) -> dict:
        self._inside(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if set(value) != {"payload", "local_mac"} or not hmac.compare_digest(value["local_mac"], self._mac(value["payload"])):
                raise RegistryError("LOCAL_RECORD_AUTHENTICATION_FAILED")
            return value["payload"]
        except RegistryError:
            raise
        except Exception as exc:
            raise RegistryError("LOCAL_RECORD_AUTHENTICATION_FAILED") from exc

    def _directory(self, case_id: str) -> Path:
        if not isinstance(case_id, str) or not re.fullmatch(r"case_[0-9a-f]{32}", case_id):
            raise RegistryError("CASE_NOT_FOUND")
        path = self._inside(self.root / "cases" / case_id)
        if not (path / "case.json").is_file():
            raise RegistryError("CASE_NOT_FOUND")
        return path

    def _base(self, case_id: str) -> tuple[Path, dict]:
        directory = self._directory(case_id)
        case = self._read(directory / "case.json")
        raw = self._inside(directory / "dataset.csv").read_bytes()
        if digest(raw) != case["asset"]["asset_sha256"]:
            raise RegistryError("ASSET_BYTES_CHANGED")
        return directory, case

    @staticmethod
    def _binding(case: dict, request: dict):
        for name in ("asset_sha256", "asset_version"):
            if request.get(name) != case["asset"][name]:
                raise RegistryError(name.upper() + "_MISMATCH")

    def register_case(self, payload: dict) -> dict:
        allowed = {"asset_id", "asset_version", "asset_sha256", "dataset_csv_base64", "task_id", "entity_key",
                   "entity_value", "field", "observation_target", "observation_rule", "capture_time", "source",
                   "authorization", "evidence", "allowed_outcomes"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise RegistryError("UNKNOWN_CASE_FIELDS")
        asset_id = _identifier(payload.get("asset_id"), "ASSET_ID")
        asset_version = _text(payload.get("asset_version"), "ASSET_VERSION", 160)
        asset_sha = _sha(payload.get("asset_sha256"), "ASSET_SHA256")
        csv_raw = _decode(payload.get("dataset_csv_base64"), MAX_CSV_BYTES, "CSV")
        if digest(csv_raw) != asset_sha:
            raise RegistryError("ASSET_SHA256_MISMATCH")
        try:
            text = csv_raw.decode("utf-8-sig")
            if "\0" in text:
                raise RegistryError("CSV_NUL_NOT_ALLOWED")
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = next(reader)
            rows = list(reader)
        except RegistryError:
            raise
        except (UnicodeError, csv.Error, StopIteration) as exc:
            raise RegistryError("VALID_UTF8_CSV_REQUIRED") from exc
        if not 1 <= len(header) <= MAX_COLUMNS or len(set(header)) != len(header) or any(not s.strip() for s in header):
            raise RegistryError("CSV_SCHEMA_INVALID")
        if not rows or any(len(row) != len(header) for row in rows):
            raise RegistryError("CSV_ROWS_INVALID")
        entity_key = _text(payload.get("entity_key"), "ENTITY_KEY", 256)
        entity_value = _text(payload.get("entity_value"), "ENTITY_VALUE", 1024)
        field = _text(payload.get("field"), "FIELD", 256)
        if entity_key not in header or field not in header:
            raise RegistryError("MATCH_KEY_OR_FIELD_NOT_IN_CSV")
        positions = [i for i, row in enumerate(rows) if row[header.index(entity_key)] == entity_value]
        if len(positions) != 1:
            raise RegistryError("ENTITY_MATCH_MUST_EXIST_AND_BE_UNIQUE")
        authorization = payload.get("authorization")
        if (not isinstance(authorization, dict) or set(authorization) - {"local_processing", "external_model_upload"}
                or authorization.get("local_processing") is not True
                or type(authorization.get("external_model_upload", False)) is not bool):
            raise RegistryError("EXPLICIT_LOCAL_PROCESSING_AUTHORIZATION_REQUIRED")
        source = payload.get("source")
        if not isinstance(source, dict) or set(source) - {"description", "url", "license", "independence_declaration"}:
            raise RegistryError("SOURCE_DECLARATION_INVALID")
        source = {name: _text(source.get(name), "SOURCE_" + name.upper(), empty=name == "url")
                  for name in ("description", "url", "license", "independence_declaration")}
        capture = payload.get("capture_time")
        if capture is not None:
            _text(capture, "CAPTURE_TIME", 100)
            try:
                datetime.fromisoformat(capture.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RegistryError("CAPTURE_TIME_INVALID") from exc
        outcomes = payload.get("allowed_outcomes", list(STATES))
        if (not isinstance(outcomes, list) or not outcomes or len(set(outcomes)) != len(outcomes)
                or any(s not in STATES for s in outcomes) or "INCOMPARABLE" not in outcomes):
            raise RegistryError("ALLOWED_OUTCOMES_MUST_RETAIN_UNKNOWN")
        protocol = {"method_version": METHOD_VERSION, "task_id": _identifier(payload.get("task_id"), "TASK_ID"),
                    "entity_key": entity_key, "entity_value": entity_value, "field": field,
                    "observation_target": _text(payload.get("observation_target"), "OBSERVATION_TARGET"),
                    "observation_rule": _text(payload.get("observation_rule"), "OBSERVATION_RULE"),
                    "capture_time": capture, "capture_time_status": "DECLARED_NOT_VERIFIED",
                    "allowed_outcomes": outcomes, "source": source}
        evidence_payloads = payload.get("evidence")
        if not isinstance(evidence_payloads, list) or not 1 <= len(evidence_payloads) <= MAX_EVIDENCE:
            raise RegistryError("ONE_TO_FOUR_EVIDENCE_FILES_REQUIRED")
        evidence, images, identifiers = [], [], set()
        for entry in evidence_payloads:
            if not isinstance(entry, dict) or set(entry) != {"evidence_id", "filename", "sha256", "data_base64"}:
                raise RegistryError("EVIDENCE_FIELDS_INVALID_SELF_REPORTED_RECEIPTS_NOT_ACCEPTED")
            evidence_id = _identifier(entry.get("evidence_id"), "EVIDENCE_ID")
            if evidence_id in identifiers:
                raise RegistryError("DUPLICATE_EVIDENCE_ID")
            identifiers.add(evidence_id)
            filename = _text(entry.get("filename"), "FILENAME", 180)
            if Path(filename).name != filename or "/" in filename or "\\" in filename or ":" in filename:
                raise RegistryError("SAFE_FILENAME_REQUIRED")
            raw = _decode(entry.get("data_base64"), MAX_IMAGE_BYTES, "IMAGE")
            info = _image(raw, filename)
            if info["sha256"] != _sha(entry.get("sha256"), "EVIDENCE_SHA256"):
                raise RegistryError("EVIDENCE_SHA256_MISMATCH")
            info.update(evidence_id=evidence_id, filename=filename, size_bytes=len(raw),
                        stored_name="image_" + evidence_id + (".png" if info["media_type"] == "image/png" else ".jpg"),
                        source_status="DECLARED_NOT_AUTHENTICATED", relevance_status="DECLARED_NOT_VERIFIED")
            evidence.append(info)
            images.append(raw)
        case_id = "case_" + uuid.uuid4().hex
        asset = {"asset_id": asset_id, "asset_version": asset_version, "asset_sha256": asset_sha}
        proposition = {"asset": asset, "entity_key": entity_key, "entity_value": entity_value, "field": field,
                       "observation_target": protocol["observation_target"], "observation_rule": protocol["observation_rule"],
                       "capture_time": capture}
        protocol_hash = digest(canonical({"asset": asset, "protocol": protocol, "evidence": evidence}))
        duplicate_rows = len(rows) - len({tuple(row) for row in rows})
        content_indices = [i for i, name in enumerate(header) if name.casefold() not in {"row_id", "_row_id", "system_row_id"}]
        content_duplicate = len(rows) - len({tuple(row[i] for i in content_indices) for row in rows}) if content_indices else None
        case = {"case_id": case_id, "created_at": _now(), "method_version": METHOD_VERSION,
                "asset": asset, "protocol": protocol, "protocol_sha256": protocol_hash,
                "proposition_sha256": digest(canonical(proposition)), "evidence": evidence,
                "authorization": {"local_processing": True, "external_model_upload": authorization.get("external_model_upload", False)},
                "dataset": {"row_count": len(rows), "column_count": len(header), "columns": header,
                            "matched_row_position": positions[0], "registry_whole_row_duplicate_count": duplicate_rows,
                            "content_duplicate_count_excluding_system_ids": content_duplicate,
                            "excluded_system_id_columns": [s for s in header if s.casefold() in {"row_id", "_row_id", "system_row_id"}],
                            "task_feature_duplicate_count": None, "fact_duplicate_status": "UNKNOWN"}}
        with self._locked():
            for path in sorted((self.root / "cases").glob("case_*/case.json")):
                old = self._read(path)
                if old["asset"]["asset_id"] == asset_id and old["asset"]["asset_version"] == asset_version and old["asset"]["asset_sha256"] != asset_sha:
                    raise RegistryError("ASSET_VERSION_ALREADY_BOUND_TO_DIFFERENT_BYTES")
            temporary = self.root / "cases" / (".register-" + uuid.uuid4().hex)
            temporary.mkdir()
            (temporary / "validations").mkdir()
            (temporary / "runs").mkdir()
            (temporary / "dataset.csv").write_bytes(csv_raw)
            for info, raw in zip(evidence, images):
                (temporary / info["stored_name"]).write_bytes(raw)
            self._write(temporary / "case.json", case)
            temporary.rename(self.root / "cases" / case_id)
        return self.get_case(case_id)

    def evidence_bytes(self, case_id: str, evidence_id: str) -> tuple[bytes, str]:
        directory, case = self._base(case_id)
        evidence = next((e for e in case["evidence"] if e["evidence_id"] == evidence_id), None)
        if evidence is None:
            raise RegistryError("EVIDENCE_NOT_FOUND")
        path = self._inside(directory / evidence["stored_name"])
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise RegistryError("EVIDENCE_BYTES_MISSING") from exc
        if digest(raw) != evidence["sha256"] or len(raw) != evidence["size_bytes"]:
            raise RegistryError("EVIDENCE_BYTES_CHANGED")
        decoded = _image(raw, evidence["filename"])
        if decoded["pixel_sha256"] != evidence["pixel_sha256"]:
            raise RegistryError("EVIDENCE_PIXELS_CHANGED")
        return raw, evidence["media_type"]

    def validate_evidence(self, case_id: str, evidence_id: str) -> dict:
        directory, case = self._base(case_id)
        raw, _ = self.evidence_bytes(case_id, evidence_id)
        evidence = next(e for e in case["evidence"] if e["evidence_id"] == evidence_id)
        receipt = {"receipt_id": "validation_" + uuid.uuid4().hex, "validated_at": _now(),
                   "case_id": case_id, "asset": case["asset"], "evidence_id": evidence_id,
                   "evidence_sha256": evidence["sha256"], "pixel_sha256": evidence["pixel_sha256"],
                   "protocol_sha256": case["protocol_sha256"], "proposition_sha256": case["proposition_sha256"],
                   "source_declaration_sha256": digest(canonical(case["protocol"]["source"])),
                   "status": "VALIDATED_LOCAL_INTEGRITY", "validation_method": "SERVER_OWNED_BYTES_BINDING_AND_HMAC_V1",
                   "source_identity_status": "NOT_VERIFIED", "relevance_status": "NOT_VERIFIED",
                   "independence_status": "NOT_VERIFIED", "fact_status": "UNKNOWN", "trusted_assertion": None}
        if self._trusted_verifier is not None:
            context = {"case_id": case_id, "asset": copy.deepcopy(case["asset"]), "evidence": copy.deepcopy(evidence),
                       "source": copy.deepcopy(case["protocol"]["source"]), "protocol": copy.deepcopy(case["protocol"]),
                       "protocol_sha256": case["protocol_sha256"], "proposition_sha256": case["proposition_sha256"]}
            try:
                attestation = self._trusted_verifier(context, raw)
            except Exception as exc:
                raise RegistryError("CONFIGURED_LOCAL_VERIFICATION_FAILED") from exc
            if not isinstance(attestation, dict):
                raise RegistryError("CONFIGURED_VERIFIER_RESULT_INVALID")
            if attestation.get("source_identity_verified") is True:
                receipt["source_identity_status"] = "VERIFIED_BY_CONFIGURED_LOCAL_WORKFLOW"
                receipt["source_verification"] = {
                    "verifier_id": _text(attestation.get("verifier_id"), "VERIFIER_ID", 256),
                    "scope": _text(attestation.get("scope"), "VERIFIER_SCOPE"),
                    "source_declaration_sha256": receipt["source_declaration_sha256"],
                    "fact_status": "UNKNOWN"}
            if attestation.get("relevance_verified") is True:
                receipt["relevance_status"] = "VERIFIED_BY_CONFIGURED_LOCAL_WORKFLOW"
            if attestation.get("assertion") is not None:
                if (attestation.get("assertion") not in {"SUPPORT", "CONTRADICT"}
                        or attestation.get("source_identity_verified") is not True
                        or attestation.get("relevance_verified") is not True
                        or attestation.get("proposition_sha256") != case["proposition_sha256"]):
                    raise RegistryError("TRUSTED_ASSERTION_NOT_BOUND_OR_AUTHENTICATED")
                receipt["trusted_assertion"] = {"assertion": attestation["assertion"],
                    "verifier_id": _text(attestation.get("verifier_id"), "VERIFIER_ID", 256),
                    "scope": _text(attestation.get("scope"), "VERIFIER_SCOPE"),
                    "proposition_sha256": attestation["proposition_sha256"],
                    "synthetic": attestation.get("synthetic") is True}
                receipt["source_identity_status"] = "VERIFIED_BY_CONFIGURED_LOCAL_WORKFLOW"
                receipt["relevance_status"] = "VERIFIED_BY_CONFIGURED_LOCAL_WORKFLOW"
        with self._locked():
            self._write(directory / "validations" / (receipt["receipt_id"] + ".json"), receipt)
        return copy.deepcopy(receipt)

    def _validations(self, directory: Path, case: dict) -> list[dict]:
        receipts = [self._read(p) for p in sorted((directory / "validations").glob("validation_*.json"))]
        for receipt in receipts:
            evidence = next((e for e in case["evidence"] if e["evidence_id"] == receipt.get("evidence_id")), None)
            if (evidence is None or receipt.get("case_id") != case["case_id"] or receipt.get("asset") != case["asset"]
                    or receipt.get("protocol_sha256") != case["protocol_sha256"]
                    or receipt.get("proposition_sha256") != case["proposition_sha256"]
                    or receipt.get("evidence_sha256") != evidence["sha256"] or receipt.get("pixel_sha256") != evidence["pixel_sha256"]
                    or receipt.get("source_declaration_sha256") != digest(canonical(case["protocol"]["source"]))):
                raise RegistryError("VALIDATION_RECEIPT_BINDING_MISMATCH")
            self.evidence_bytes(case["case_id"], evidence["evidence_id"])
        return receipts

    def _executions(self, directory: Path, case: dict) -> list[dict]:
        result = []
        for started_path in sorted((directory / "runs").glob("run_*.started.json")):
            started = self._read(started_path)
            final_path = directory / "runs" / (started["run_id"] + ".json")
            execution = self._read(final_path) if final_path.is_file() else started
            if (execution.get("case_id") != case["case_id"] or execution.get("asset") != case["asset"]
                    or execution.get("protocol_sha256") != case["protocol_sha256"]):
                raise RegistryError("EXECUTION_BINDING_MISMATCH")
            result.append(execution)
        return result

    def get_case(self, case_id: str) -> dict:
        directory, case = self._base(case_id)
        # Every read verifies original bytes. A source string is never a trust anchor.
        for evidence in case["evidence"]:
            self.evidence_bytes(case_id, evidence["evidence_id"])
        validations = self._validations(directory, case)
        executions = self._executions(directory, case)
        reviews = [e["review"] for e in executions if e.get("review")]
        pixels = {e["pixel_sha256"] for e in case["evidence"]}
        assertions = [(r["pixel_sha256"], r["trusted_assertion"]["assertion"])
                      for r in validations if r.get("trusted_assertion")]
        conflict = any(p1 != p2 and a1 != a2 for p1, a1 in assertions for p2, a2 in assertions)
        outcomes = {r["outcome"] for r in reviews}
        if conflict:
            coordination = "EVIDENCE_CONFLICT"
        elif len(outcomes) > 1:
            coordination = "REVIEW_DISAGREEMENT"
        elif outcomes == {"MATCH"}:
            coordination = "NARROW_OBSERVATION_SUPPORTED"
        else:
            coordination = "ADDITIONAL_EVIDENCE_REQUIRED"
        counts = Counter(r["outcome"] for r in reviews)
        case.update(validations=validations, executions=executions, reviews=reviews,
                    source_status="DECLARED_NOT_AUTHENTICATED", coordination_state=coordination,
                    deduplication={"evidence_file_count": len(case["evidence"]), "unique_byte_count": len({e["sha256"] for e in case["evidence"]}),
                        "unique_pixel_observation_count": len(pixels), "independent_physical_observation_count": None,
                        "review_count": len(reviews), "additional_physical_observations_from_repeat_reviews": 0,
                        "scope": "CANONICAL_PIXELS_DEDUPLICATED_INDEPENDENT_ACQUISITIONS_NOT_CERTIFIED"},
                    assessment={"fact_status": "UNKNOWN", "decision": "NEED_MORE_EVIDENCE",
                        "content_authenticity_status": "UNKNOWN", "coordination_state": coordination,
                        "task_value": {"status": "NOT_EVALUATED", "reason": "NO_EXACT_TASK_MEASUREMENT_BOUND_TO_THIS_CASE"},
                        "population_error_interval": None, "statistical_guarantee": "NOT_APPLICABLE_NO_PROBABILITY_AUDIT_OR_FACT_TRUTH",
                        "selected_entity_count": 1, "unknown_or_unconfirmed_entity_count": 1,
                        "outcome_counts": {s: counts.get(s, 0) for s in STATES},
                        "supplementary_evidence_needed": ["核验来源身份、采集时间与现实实体对应关系。", "AI影像支持不能替代独立事实核验。", "没有概率审计与可靠真值，不报告总体真实性频率保证。"]})
        return case

    def list_cases(self) -> list[dict]:
        return [self.get_case(p.parent.name) for p in sorted((self.root / "cases").glob("case_*/case.json"))]

    def execute(self, case_id: str, request: dict, adapter=None) -> dict:
        if not isinstance(request, dict) or set(request) - {"mode", "asset_sha256", "asset_version", "evidence_id", "external_upload_authorized", "run_id"}:
            raise RegistryError("EXECUTION_FIELDS_INVALID")
        directory, case = self._base(case_id)
        self._binding(case, request)
        if request.get("mode") == "REPLAY":
            self.evidence_bytes(case_id, request.get("evidence_id"))
            replayed = self.replay(case_id, request.get("run_id"))
            if request.get("evidence_id") != replayed["evidence_id"]:
                raise RegistryError("EVIDENCE_ID_MISMATCH")
            return replayed
        if request.get("mode") != "LIVE":
            raise RegistryError("LIVE_OR_REPLAY_REQUIRED")
        evidence_id = request.get("evidence_id")
        raw, _ = self.evidence_bytes(case_id, evidence_id)
        evidence = next(e for e in case["evidence"] if e["evidence_id"] == evidence_id)
        local_validation = self.validate_evidence(case_id, evidence_id)
        availability = adapter.availability() if adapter is not None else {"available": False, "reason": "LIVE_UNAVAILABLE"}
        if not isinstance(availability, dict) or type(availability.get("available")) is not bool:
            raise RegistryError("ADAPTER_AVAILABILITY_INVALID")
        if availability["available"]:
            if (case["authorization"]["external_model_upload"] is not True
                    or request.get("external_upload_authorized") is not True):
                raise RegistryError("EXTERNAL_UPLOAD_NOT_AUTHORIZED")
        run_id = "run_" + uuid.uuid4().hex
        result = {"run_id": run_id, "case_id": case_id, "asset": case["asset"], "protocol_sha256": case["protocol_sha256"],
                  "evidence_id": evidence_id, "evidence_sha256": evidence["sha256"], "pixel_sha256": evidence["pixel_sha256"],
                  "validation_receipt_id": local_validation["receipt_id"], "requested_mode": "LIVE", "execution_mode": "RUNNING",
                  "status": "RUNNING", "started_at": _now(), "model_invoked": False, "request_attempted": False,
                  "fallback_performed": False, "reason": None, "error_type": None, "receipt": None, "review": None}
        with self._locked():
            self._write(directory / "runs" / (run_id + ".started.json"), result)
        if not availability["available"]:
            result.update(status="UNAVAILABLE", execution_mode="UNAVAILABLE", reason="LIVE_UNAVAILABLE",
                          availability_reason=availability.get("reason", "NO_CONFIGURED_ADAPTER"))
        else:
            context = {"case_id": case_id, "asset": copy.deepcopy(case["asset"]), "task_id": case["protocol"]["task_id"],
                       "protocol": copy.deepcopy(case["protocol"]), "protocol_sha256": case["protocol_sha256"],
                       "proposition_sha256": case["proposition_sha256"], "evidence_id": evidence_id,
                       "input_sha256": evidence["sha256"], "pixel_sha256": evidence["pixel_sha256"],
                       "review_run_id": run_id, "authorization": {"external_upload_authorized": True}}
            context.update(case["asset"])
            context.update({name: case["protocol"][name] for name in
                            ("field", "observation_target", "observation_rule", "entity_key", "entity_value", "capture_time")})
            context.update(evidence_sha256=evidence["sha256"], external_upload_authorized=True)
            # Entering an adapter is not proof that a provider request was sent
            # or a model ran. Preserve unknown until a bound receipt says more.
            result["adapter_invocation_attempted"] = True
            result["request_attempted"] = None
            result["model_invoked"] = None
            try:
                output = adapter.review(raw, context)
                receipt, review = self._normalize_output(output, case, evidence, run_id, availability)
                result.update(status="LIVE", execution_mode="LIVE", model_invoked=True,
                              request_attempted=True, receipt=receipt, review=review, synthetic=receipt.get("synthetic") is True)
                result["raw_structured_review"] = {name: copy.deepcopy(output.get(name)) for name in
                    ("outcome", "observation_basis", "visible_features", "competing_interpretations", "uncertainty_reason")}
                result["raw_structured_review"]["receipt"] = copy.deepcopy(receipt)
            except Exception as exc:
                is_timeout = isinstance(exc, TimeoutError) or type(exc).__name__ in {"AdapterTimeout", "TimeoutException", "APITimeoutError"}
                result.update(status="FAILED", execution_mode="FAILED", error_type="TIMEOUT" if is_timeout else "ADAPTER_ERROR",
                              reason="MODEL_CALL_TIMEOUT" if is_timeout else "MODEL_CALL_OR_BOUND_RESULT_VALIDATION_FAILED",
                              error_class=type(exc).__name__)
                failure_receipt = getattr(exc, "receipt", None)
                if isinstance(failure_receipt, dict):
                    allowed = {"call_id", "provider", "model", "requested_model", "actual_model", "model_version",
                        "request_id", "request_id_type", "called_at", "input_sha256", "protocol_sha256", "execution_mode",
                        "usage", "elapsed_seconds", "raw_response_sha256", "raw_events_sha256", "request_attempted",
                        "model_invoked", "synthetic", "finished_at", "reason", "error_class"}
                    safe = {name: copy.deepcopy(failure_receipt[name]) for name in allowed if name in failure_receipt}
                    if (safe.get("input_sha256", evidence["sha256"]) == evidence["sha256"]
                            and safe.get("protocol_sha256", case["protocol_sha256"]) == case["protocol_sha256"]):
                        canonical(safe)
                        result["receipt"] = safe
                        for name in ("model_invoked", "request_attempted"):
                            value = safe.get(name)
                            result[name] = value if value is None or type(value) is bool else None
        result["finished_at"] = _now()
        result["result_sha256"] = digest(canonical(result))
        with self._locked():
            self._write(directory / "runs" / (run_id + ".json"), result)
        return copy.deepcopy(result)

    @staticmethod
    def _normalize_output(output, case: dict, evidence: dict, run_id: str, availability: dict) -> tuple[dict, dict]:
        if not isinstance(output, dict) or output.get("outcome") not in case["protocol"]["allowed_outcomes"]:
            raise RegistryError("STRUCTURED_FOUR_STATE_REVIEW_REQUIRED")
        receipt = output.get("receipt")
        required = {"provider", "model", "request_id", "called_at", "input_sha256", "execution_mode", "usage"}
        if not isinstance(receipt, dict) or not required.issubset(receipt):
            raise RegistryError("ACTUAL_MODEL_CALL_RECEIPT_REQUIRED")
        if receipt["input_sha256"] != evidence["sha256"] or receipt["execution_mode"] != "LIVE":
            raise RegistryError("MODEL_RECEIPT_INPUT_BINDING_MISMATCH")
        if receipt.get("protocol_sha256", case["protocol_sha256"]) != case["protocol_sha256"]:
            raise RegistryError("MODEL_RECEIPT_PROTOCOL_BINDING_MISMATCH")
        if output.get("protocol_sha256", case["protocol_sha256"]) != case["protocol_sha256"]:
            raise RegistryError("MODEL_OUTPUT_PROTOCOL_BINDING_MISMATCH")
        if output.get("input_sha256", evidence["sha256"]) != evidence["sha256"]:
            raise RegistryError("MODEL_OUTPUT_INPUT_BINDING_MISMATCH")
        for name in ("provider", "model", "request_id", "called_at"):
            _text(receipt.get(name), "MODEL_RECEIPT_" + name.upper(), 512)
        for name in ("provider", "model"):
            if (availability.get(name) and receipt[name] != availability[name]
                    and not (name == "model" and receipt.get("requested_model") == availability[name])):
                raise RegistryError("MODEL_RECEIPT_CONFIG_IDENTITY_MISMATCH")
        try:
            called = datetime.fromisoformat(receipt["called_at"].replace("Z", "+00:00"))
            if called.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise RegistryError("MODEL_RECEIPT_TIME_INVALID") from exc
        if receipt["usage"] is not None and not isinstance(receipt["usage"], dict):
            raise RegistryError("MODEL_RECEIPT_USAGE_INVALID")
        # Only these fields may enter a persisted receipt: never arbitrary SDK
        # headers, environment variables, API keys or a full HTTP transcript.
        receipt = {name: copy.deepcopy(receipt[name]) for name in required | {"requested_model", "request_id_type", "model_version", "actual_model", "model_is_requested_alias", "call_id", "raw_response_sha256", "raw_events_sha256", "protocol_sha256", "elapsed_seconds", "request_attempted", "model_invoked"} if name in receipt}
        if output["receipt"].get("synthetic") is True:
            receipt["synthetic"] = True
        canonical(receipt)
        fields = {}
        for name in ("observation_basis", "uncertainty_reason"):
            fields[name] = _text(output.get(name, ""), name.upper(), empty=True)
        for name in ("visible_features", "competing_interpretations"):
            values = output.get(name, [])
            if isinstance(values, str):
                values = [values] if values else []
            if not isinstance(values, list) or len(values) > 30:
                raise RegistryError("STRUCTURED_REVIEW_LIST_INVALID")
            fields[name] = [_text(v, name.upper(), 2048) for v in values]
        review = {"review_id": "review_" + uuid.uuid4().hex, "review_run_id": run_id, "reviewer_type": "AI",
                  "execution_mode": "LIVE", "asset": copy.deepcopy(case["asset"]), "evidence_id": evidence["evidence_id"],
                  "evidence_sha256": evidence["sha256"], "pixel_sha256": evidence["pixel_sha256"],
                  "protocol_sha256": case["protocol_sha256"], "proposition_sha256": case["proposition_sha256"],
                  "outcome": output["outcome"], "model_id": receipt["model"], "provider": receipt["provider"],
                  "request_id": receipt["request_id"], "review_time": receipt["called_at"],
                  "image_actually_read_by_adapter": True, "fact_status": "UNKNOWN", **fields}
        review["result_sha256"] = digest(canonical(review))
        return receipt, review

    def replay(self, case_id: str, run_id: str) -> dict:
        directory, case = self._base(case_id)
        if not isinstance(run_id, str) or not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise RegistryError("RUN_NOT_FOUND")
        path = directory / "runs" / (run_id + ".json")
        if not path.is_file():
            raise RegistryError("RUN_NOT_FOUND")
        original = self._read(path)
        if original.get("case_id") != case_id or original.get("asset") != case["asset"] or original.get("protocol_sha256") != case["protocol_sha256"]:
            raise RegistryError("EXECUTION_BINDING_MISMATCH")
        if original.get("status") != "LIVE" or not original.get("review"):
            raise RegistryError("REPLAY_REQUIRES_ACTUAL_REVIEW_RESULT")
        self.evidence_bytes(case_id, original["evidence_id"])
        replayed = {**copy.deepcopy(original), "status": "REPLAY", "execution_mode": "REPLAY",
                "original_execution_mode": original["execution_mode"], "original_result_sha256": original["result_sha256"],
                "model_invoked": False, "request_attempted": False, "replayed_at": _now(), "fallback_performed": False}
        replayed.pop("result_sha256", None)
        replayed["result_sha256"] = digest(canonical(replayed))
        return replayed

    def report(self, case_id: str) -> dict:
        case = self.get_case(case_id)
        asset = case["asset"]
        lines = ["# 数据集视觉证据与任务价值联合评估", "", "## 精确版本与观察规则", "",
                 f"- 资产：{asset['asset_id']} / {asset['asset_version']}", f"- 数据集SHA-256：{asset['asset_sha256']}",
                 f"- 观察目标：{case['protocol']['observation_target']}", f"- 固定规则：{case['protocol']['observation_rule']}",
                 f"- 协议SHA-256：{case['protocol_sha256']}", "", "## 真实执行与证据边界", "",
                 f"- 审阅记录：{len(case['reviews'])}；去重后像素证据：{case['deduplication']['unique_pixel_observation_count']}。",
                 "- 重复AI判读新增独立物理观测为0；不同像素文件也未自动认证为独立采集。",
                 "- 来源与拍摄时间为声明，只有配置的本地可信流程才可提升相应证据身份状态。",
                 f"- 协调状态：{case['coordination_state']}。", "- 内容事实真实性：UNKNOWN；条件判断：NEED_MORE_EVIDENCE。",
                 "- 无概率抽样与可靠真值，未计算总体真实性风险区间；AI一致率不是事实准确率。",
                 "- 任务价值：NOT_EVALUATED；没有为此精确资产绑定合法任务实测。", "", "## 执行历史", ""]
        for execution in case["executions"]:
            lines.append(f"- {execution['run_id']}：{execution['status']}，模型实际成功调用确认={execution['model_invoked']}，回退={execution['fallback_performed']}。")
        lines.extend(["", "## 逐条结果", ""])
        for review in case["reviews"]:
            lines.append(f"- {review['evidence_id']}：{review['outcome']}；{review['observation_basis']}；不确定性：{review['uncertainty_reason']}")
        case["report_markdown"] = "\n".join(lines) + "\n"
        return case
