from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import data_dir, db_path, state_dir, state_json_path

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    recorded_at_utc TEXT NOT NULL,
    local_tz TEXT NOT NULL,
    client TEXT NOT NULL,
    trigger TEXT NOT NULL,
    transcription TEXT NOT NULL,
    classifier TEXT NOT NULL,
    action TEXT,
    action_args_json TEXT,
    dispatch_status TEXT NOT NULL,
    action_result TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    action TEXT NOT NULL,
    dispatch_status TEXT NOT NULL,
    action_result TEXT,
    error TEXT,
    forced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES events(id)
);
"""


@dataclass
class Event:
    id: str
    recorded_at_utc: str
    local_tz: str
    client: str
    trigger: str
    transcription: str
    classifier: str
    action: str | None
    action_args_json: str | None
    dispatch_status: str
    action_result: str | None
    error: str | None
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        preview = self.transcription.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        return {
            "id": self.id,
            "id8": self.id[:8],
            "recordedAt": self.recorded_at_utc,
            "client": self.client,
            "trigger": self.trigger,
            "preview": preview,
            "transcription": self.transcription,
            "classifier": self.classifier,
            "action": self.action,
            "dispatchStatus": self.dispatch_status,
            "actionResult": self.action_result,
            "error": self.error,
            "createdAt": self.created_at,
        }


def _connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    data_dir().chmod(0o700)
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    db_path().chmod(0o600)
    return conn


_CONN: sqlite3.Connection | None = None


def reset_connection() -> None:
    global _CONN
    if _CONN is not None:
        _CONN.close()
        _CONN = None


def connection() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = _connect()
    return _CONN


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        recorded_at_utc=row["recorded_at_utc"],
        local_tz=row["local_tz"],
        client=row["client"],
        trigger=row["trigger"],
        transcription=row["transcription"],
        classifier=row["classifier"],
        action=row["action"],
        action_args_json=row["action_args_json"],
        dispatch_status=row["dispatch_status"],
        action_result=row["action_result"],
        error=row["error"],
        created_at=row["created_at"],
    )


def insert_event(
    *,
    event_id: str,
    recorded_at_utc: str,
    local_tz: str,
    client: str,
    trigger: str,
    transcription: str,
    classifier: str,
    dispatch_status: str,
) -> bool:
    """Return True if inserted, False if duplicate."""
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        conn = connection()
        try:
            conn.execute(
                """
                INSERT INTO events (
                    id, recorded_at_utc, local_tz, client, trigger, transcription,
                    classifier, dispatch_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    recorded_at_utc,
                    local_tz,
                    client,
                    trigger,
                    transcription,
                    classifier,
                    dispatch_status,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return False
    write_state_snapshot()
    return True


def get_event(event_id: str) -> Event | None:
    with _LOCK:
        row = connection().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_event(row) if row else None


def list_events(limit: int = 50) -> list[Event]:
    with _LOCK:
        rows = connection().execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def mark_event(
    event_id: str,
    *,
    action: str | None = None,
    action_args: dict[str, Any] | None = None,
    dispatch_status: str,
    action_result: str | None = None,
    error: str | None = None,
    forced: bool = False,
) -> None:
    args_json = json.dumps(action_args) if action_args is not None else None
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        conn = connection()
        conn.execute(
            """
            UPDATE events SET
                action = COALESCE(?, action),
                action_args_json = COALESCE(?, action_args_json),
                dispatch_status = ?,
                action_result = ?,
                error = ?
            WHERE id = ?
            """,
            (action, args_json, dispatch_status, action_result, error, event_id),
        )
        conn.execute(
            """
            INSERT INTO dispatches (
                event_id, action, dispatch_status, action_result, error, forced, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, action or "", dispatch_status, action_result, error, int(forced), now),
        )
        conn.commit()
    write_state_snapshot()


def write_state_snapshot() -> None:
    events = list_events(40)
    pending = sum(1 for event in events if event.dispatch_status == "pending")
    failed = sum(1 for event in events if event.dispatch_status == "failed")
    payload = {
        "online": True,
        "pending": pending,
        "failed": failed,
        "total": len(events),
        "events": [event.to_public_dict() for event in events],
    }
    target = state_json_path()
    state_dir().mkdir(parents=True, exist_ok=True)
    state_dir().chmod(0o700)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(target)
    target.chmod(0o600)


def offline_state() -> dict[str, Any]:
    path = state_json_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["online"] = False
            return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"online": False, "pending": 0, "failed": 0, "total": 0, "events": []}
