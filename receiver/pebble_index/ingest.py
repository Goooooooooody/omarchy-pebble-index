from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import ActionSpec
from .classify import Action, classify, recorded_at_from_ms
from .config import Config, load_config
from .ids import event_id
from .store import Event, get_event, insert_event

MAX_TRANSCRIPT = 16000
CLIENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
TRIGGER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def normalize_transcript(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) > MAX_TRANSCRIPT:
        return cleaned[:MAX_TRANSCRIPT]
    return cleaned


def action_view(action: Action, config: Config) -> dict[str, Any]:
    spec = config.actions().get(action.name)
    payload = {
        "action": action.name,
        "label": spec.label if spec else action.name,
        "glyph": spec.glyph if spec else "",
        "description": spec.description if spec else "",
        "title": action.title,
        "minutes": action.minutes,
        "when": action.when.isoformat() if action.when else None,
        "body": action.body,
        "prompt": action.prompt,
    }
    screenshot = str(action.extra.get("screenshot") or "")
    if screenshot:
        payload["screenshot"] = screenshot
    return payload


def spec_view(spec: ActionSpec, *, agent_enabled: bool) -> dict[str, Any]:
    return {
        "id": spec.id,
        "label": spec.label,
        "description": spec.description,
        "enabled": spec.enabled,
        "available": spec.available(agent_enabled),
        "match": spec.match,
        "priority": spec.priority,
        "builtin": spec.builtin or None,
        "context": list(spec.context),
        "glyph": spec.glyph,
        "fields": list(spec.fields),
        "wake": list(spec.wake),
        "source": str(spec.source) if spec.source else "builtin",
    }


def classify_payload(
    text: str,
    *,
    config: Config | None = None,
    recorded_at: datetime | None = None,
    screenshot: str = "",
) -> dict[str, Any]:
    loaded = config or load_config()
    transcript = normalize_transcript(text)
    if not transcript:
        raise ValueError("transcription is empty")
    when = recorded_at or datetime.now().astimezone()
    action = classify(transcript, when, loaded)
    if screenshot:
        action.extra["screenshot"] = screenshot
    return action_view(action, loaded)


def accept_transcript(
    *,
    text: str,
    client: str,
    trigger: str,
    recorded_at: str,
    config: Config,
    is_test: bool = False,
) -> dict[str, Any]:
    transcript = normalize_transcript(text)
    if not transcript and not is_test:
        raise ValueError("transcription is empty")
    if is_test and not transcript:
        transcript = "Index webhook test event"
    client = _token(client, CLIENT_RE, "voice")
    trigger = _token(trigger, TRIGGER_RE, "")
    stamp = recorded_at.strip() or str(int(datetime.now(timezone.utc).timestamp() * 1000))
    eid = event_id(client, stamp, transcript)
    recorded = recorded_at_from_ms(stamp)
    status = "test" if is_test else "pending"
    inserted = insert_event(
        event_id=eid,
        recorded_at_utc=recorded.astimezone().isoformat(),
        local_tz=str(recorded.tzinfo),
        client=client,
        trigger=trigger or ("test-event" if is_test else ""),
        transcription=transcript,
        classifier=config.classifier,
        dispatch_status=status,
    )
    if not inserted:
        return {"status": "duplicate", "id": eid}
    return {"status": status if is_test else "accepted", "id": eid}


def capture_transcript(
    text: str,
    *,
    client: str = "voice",
    trigger: str = "overlay",
    screenshot: str = "",
    config: Config | None = None,
    recorded_at: str = "",
) -> dict[str, Any]:
    """Classify and dispatch a local transcript. Does not need Tailscale."""
    from .server import process_event

    loaded = config or load_config()
    accepted = accept_transcript(
        text=text,
        client=client,
        trigger=trigger,
        recorded_at=recorded_at,
        config=loaded,
    )
    event = get_event(str(accepted["id"]))
    if event is None:
        raise RuntimeError("event was not stored")
    if accepted["status"] == "duplicate":
        return _event_payload(event, loaded, status="duplicate")
    if accepted["status"] == "test":
        return _event_payload(event, loaded, status="test")
    path = Path(screenshot).expanduser() if screenshot else None
    process_event(event, loaded, screenshot=str(path) if path and path.is_file() else "")
    stored = get_event(event.id) or event
    return _event_payload(stored, loaded, status=stored.dispatch_status)


def _event_payload(event: Event, config: Config, *, status: str) -> dict[str, Any]:
    spec = config.actions().get(event.action or "")
    payload = event.to_public_dict()
    payload["status"] = status
    payload["label"] = spec.label if spec else (event.action or "")
    payload["glyph"] = spec.glyph if spec else ""
    return payload


def _token(value: str, pattern: re.Pattern[str], default: str) -> str:
    cleaned = (value or "").strip().lower()
    if pattern.match(cleaned):
        return cleaned
    return default
