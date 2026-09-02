from __future__ import annotations

import json
import queue
import secrets
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .bind import BindError, tailscale_ipv4
from .classify import Action, classify, recorded_at_from_ms
from .config import Config, load_config
from .dispatch import DispatchError, dispatch
from .ids import event_id
from .notify import notify
from .store import Event, get_event, insert_event, list_events, mark_event, write_state_snapshot

MAX_BODY = 2 * 1024 * 1024
MAX_TRANSCRIPT = 16000
_WORK: queue.Queue[str] = queue.Queue()


def check_bearer(expected: str, header: str) -> bool:
    if not expected:
        return False
    provided = ""
    if header.lower().startswith("bearer "):
        provided = header[7:].strip()
    if not provided or len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)


def _parse_multipart(content_type: str, body: bytes) -> dict[str, str]:
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    )
    fields: dict[str, str] = {}
    if not message.is_multipart():
        return fields
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name or name == "audio":
            continue
        payload = part.get_content()
        if isinstance(payload, bytes):
            fields[str(name)] = payload.decode("utf-8", errors="replace")
        else:
            fields[str(name)] = str(payload)
    return fields


class Handler(BaseHTTPRequestHandler):
    config: Config

    def log_message(self, format: str, *args: object) -> None:
        sys_stderr_write = super().log_message
        # Keep journald free of transcript bodies; default log is just the request line.
        sys_stderr_write(format, *args)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _check_auth(self) -> bool:
        expected = self.config.token
        if not expected:
            self._json(500, {"status": "error", "detail": "token is not configured"})
            return False
        if not check_bearer(expected, self.headers.get("Authorization", "")):
            self._json(401, {"status": "unauthorized"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok", "online": True})
            return
        if self.path == "/status":
            if not self._check_auth():
                return
            events = list_events(40)
            self._json(
                200,
                {
                    "status": "ok",
                    "online": True,
                    "total": len(events),
                    "pending": sum(1 for event in events if event.dispatch_status == "pending"),
                    "failed": sum(1 for event in events if event.dispatch_status == "failed"),
                },
            )
            return
        self._json(404, {"status": "not-found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/", "/webhook"}:
            self._json(404, {"status": "not-found"})
            return
        if not self._check_auth():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"status": "too-large"})
            return
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        fields = _parse_multipart(content_type, body) if "multipart/" in content_type else {}
        transcription = (fields.get("transcription") or "").strip()
        if len(transcription) > MAX_TRANSCRIPT:
            transcription = transcription[:MAX_TRANSCRIPT]
        recorded_at = fields.get("recordedAt") or ""
        client = fields.get("client") or "ring"
        trigger = self.headers.get("X-Index-Trigger") or ""
        is_test = (
            self.headers.get("X-Index-Test", "").lower() == "true"
            or fields.get("test", "").lower() == "true"
            or trigger == "test-event"
        )
        if not transcription and not is_test:
            self._json(400, {"status": "error", "detail": "transcription required"})
            return
        if is_test and not transcription:
            transcription = "Index webhook test event"
        if not recorded_at:
            from datetime import datetime, timezone

            recorded_at = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        eid = event_id(client, recorded_at, transcription)
        recorded_dt = recorded_at_from_ms(recorded_at)
        status = "test" if is_test else "pending"
        inserted = insert_event(
            event_id=eid,
            recorded_at_utc=recorded_dt.astimezone().isoformat(),
            local_tz=str(recorded_dt.tzinfo),
            client=client,
            trigger=trigger or ("test-event" if is_test else ""),
            transcription=transcription,
            classifier=self.config.classifier,
            dispatch_status=status,
        )
        if not inserted:
            self._json(200, {"status": "duplicate", "id": eid})
            return
        if is_test:
            self._json(200, {"status": "test", "id": eid})
            return
        _WORK.put(eid)
        self._json(200, {"status": "accepted", "id": eid})


def process_event(event: Event, config: Config, *, force: bool = False, screenshot: str = "") -> None:
    if event.dispatch_status == "test":
        return
    if event.dispatch_status == "done" and not force:
        raise RuntimeError("already dispatched; pass --force to create another")
    try:
        from datetime import datetime

        try:
            recorded = datetime.fromisoformat(event.recorded_at_utc)
        except ValueError:
            recorded = recorded_at_from_ms(event.recorded_at_utc)
        action: Action = classify(event.transcription, recorded, config)
        action.extra["recorded_at"] = event.recorded_at_utc
        if screenshot:
            action.extra["screenshot"] = screenshot
        result = dispatch(action, config, event.id)
        mark_event(
            event.id,
            action=action.name,
            action_args=action.args(),
            dispatch_status="done",
            action_result=result,
            forced=force,
        )
        notify(f"{action.name}: {action.title}", result)
    except Exception as error:  # noqa: BLE001 — worker must never die on one event
        mark_event(
            event.id,
            dispatch_status="failed",
            error=str(error)[:400],
            forced=force,
        )
        notify("Index dispatch failed", str(error)[:200], critical=True)


def worker_loop(config: Config) -> None:
    while True:
        event_key = _WORK.get()
        event = get_event(event_key)
        if event is None:
            continue
        process_event(event, config)


def enqueue(event_key: str) -> None:
    _WORK.put(event_key)


def serve(config: Config | None = None) -> None:
    loaded = config or load_config()
    if not loaded.token:
        raise SystemExit("config token is empty; Start receiver in the widget")
    try:
        address = tailscale_ipv4()
    except BindError as error:
        raise SystemExit(f"refusing to start: {error}") from error
    Handler.config = loaded
    write_state_snapshot()
    thread = threading.Thread(target=worker_loop, args=(loaded,), daemon=True)
    thread.start()
    httpd = ThreadingHTTPServer((address, loaded.bind_port), Handler)
    print(f"listening on http://{address}:{loaded.bind_port}/webhook", flush=True)
    httpd.serve_forever()
