"""Small transaction-scoped SQLite store; immutable versions and receipts."""
from __future__ import annotations

from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3
from .security import canonical_bytes, canonical_hash, utcnow


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS assets(asset_id TEXT,version TEXT,provider_id TEXT,envelope TEXT,state TEXT, PRIMARY KEY(asset_id,version));
            CREATE TABLE IF NOT EXISTS asset_state_events(event_id TEXT PRIMARY KEY,asset_id TEXT,version TEXT,state TEXT,effective_at TEXT,envelope TEXT);
            CREATE INDEX IF NOT EXISTS asset_state_lookup ON asset_state_events(asset_id,version,effective_at);
            CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY,body_hash TEXT,contract TEXT);
            CREATE TABLE IF NOT EXISTS contracts(contract_id TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE IF NOT EXISTS receipts(contract_id TEXT PRIMARY KEY,body_hash TEXT,envelope TEXT,record TEXT);
            CREATE TABLE IF NOT EXISTS receipt_events(event_id TEXT PRIMARY KEY,contract_id TEXT,record TEXT);
            CREATE TABLE IF NOT EXISTS reports(report_id TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_time TEXT,kind TEXT,body TEXT,previous_hash TEXT,event_hash TEXT);
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self, query: str, args: tuple = ()) -> list[dict]:
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(query, args)]

    @staticmethod
    def audit(db, kind: str, body: dict):
        row = db.execute("SELECT event_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        previous = row[0] if row else "0" * 64
        event = {"event_time": utcnow(), "kind": kind, "body": body, "previous_hash": previous}
        db.execute("INSERT INTO audit(event_time,kind,body,previous_hash,event_hash) VALUES(?,?,?,?,?)",
            (event["event_time"], kind, canonical_bytes(body).decode(), previous, canonical_hash(event)))

    def asset(self, asset_id: str, version: str, at: str | None = None) -> dict:
        found = self.read("SELECT envelope,state FROM assets WHERE asset_id=? AND version=?", (asset_id, version))
        if not found:
            raise KeyError("ASSET_VERSION_NOT_FOUND")
        state = found[0]["state"]
        if at is not None:
            # ISO input is parsed before SQL; compare instants, not lexical offsets.
            from .security import parse_time
            events = self.read("SELECT state,effective_at FROM asset_state_events WHERE asset_id=? AND version=? ORDER BY rowid", (asset_id, version))
            visible = [r for r in events if parse_time(r["effective_at"]) <= parse_time(at)]
            if not visible:
                raise ValueError("ASSET_NOT_REGISTERED_AT_QUERY_TIME")
            state = visible[-1]["state"]
        return {**json.loads(found[0]["envelope"])["payload"], "state": state}

    def records(self) -> list[dict]:
        return [json.loads(row["record"]) for row in self.read("SELECT record FROM receipt_events ORDER BY rowid")]
