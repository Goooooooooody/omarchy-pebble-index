from __future__ import annotations

import argparse
import json
import subprocess

from .config import load_config
from .notify import notify
from .server import process_event, serve
from .store import Event, get_event, list_events, offline_state, write_state_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pebble-index")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the Tailscale webhook receiver")
    status = sub.add_parser("status", help="receiver + inbox snapshot")
    status.add_argument("--json", action="store_true")
    inbox = sub.add_parser("inbox", help="list recent events")
    inbox.add_argument("action", choices=["list"])
    inbox.add_argument("--json", action="store_true")
    inbox.add_argument("--limit", type=int, default=40)
    replay = sub.add_parser("replay", help="re-dispatch a stored event")
    replay.add_argument("event_id")
    replay.add_argument("--force", action="store_true")
    reclassify = sub.add_parser("reclassify", help="classify again and dispatch")
    reclassify.add_argument("event_id")
    reclassify.add_argument("--force", action="store_true")
    open_cmd = sub.add_parser("open", help="open the note or reveal the result")
    open_cmd.add_argument("event_id")
    actions_cmd = sub.add_parser("actions", help="list loaded actions")
    actions_cmd.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        serve()
        return 0
    if args.cmd == "status":
        return _status(args.json)
    if args.cmd == "inbox":
        events = list_events(args.limit)
        if args.json:
            print(json.dumps({"online": _receiver_up(), "events": [event.to_public_dict() for event in events]}))
        else:
            for event in events:
                print(f"{event.id[:8]}  {event.dispatch_status:8}  {event.action or '-':8}  {event.transcription[:60]}")
        return 0
    if args.cmd in {"replay", "reclassify"}:
        return _replay(args.event_id, force=args.force)
    if args.cmd == "open":
        return _open(args.event_id)
    if args.cmd == "actions":
        return _actions(args.json)
    return 2


def _receiver_up() -> bool:
    try:
        config = load_config()
    except (OSError, ValueError, FileNotFoundError):
        return False
    try:
        from .bind import tailscale_ipv4
        import urllib.request

        address = tailscale_ipv4()
        with urllib.request.urlopen(f"http://{address}:{config.bind_port}/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _status(as_json: bool) -> int:
    online = _receiver_up()
    if online:
        write_state_snapshot()
        events = list_events(40)
        payload = {
            "online": True,
            "pending": sum(1 for event in events if event.dispatch_status == "pending"),
            "failed": sum(1 for event in events if event.dispatch_status == "failed"),
            "total": len(events),
            "events": [event.to_public_dict() for event in events],
        }
    else:
        payload = offline_state()
        payload["online"] = False
    if as_json:
        print(json.dumps(payload))
    else:
        print("online" if payload.get("online") else "offline")
        print(f"events={payload.get('total', 0)} pending={payload.get('pending', 0)} failed={payload.get('failed', 0)}")
    return 0


def _replay(event_id: str, *, force: bool) -> int:
    event = _resolve(event_id)
    if event is None:
        print("event not found", flush=True)
        return 1
    if event.dispatch_status == "test":
        print("test events are not dispatched")
        return 2
    config = load_config()
    try:
        process_event(event, config, force=force)
    except Exception as error:
        print(error)
        return 1
    print("dispatched")
    return 0


def _actions(as_json: bool) -> int:
    config = load_config()
    rows = []
    for spec in config.actions().all():
        origin = str(spec.source) if spec.source else "builtin"
        rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "enabled": spec.enabled,
                "available": spec.available(config.agent_enabled),
                "match": spec.match,
                "priority": spec.priority,
                "builtin": spec.builtin or None,
                "source": origin,
            }
        )
    if as_json:
        print(json.dumps({"actions": rows}))
        return 0
    for row in rows:
        flag = "on " if row["available"] else "off"
        kind = row["builtin"] or "command"
        print(f"{row['id']:<16} {flag}  {kind:<8} {row['match']:<8} {row['source']}")
    return 0


def _open(event_id: str) -> int:
    event = _resolve(event_id)
    if event is None:
        print("event not found")
        return 1
    result = event.action_result or ""
    if result.startswith("calendar-failed-note:"):
        result = result.split(":", 1)[1]
    if event.action == "note" or result.endswith(".md"):
        path = result if result.endswith(".md") else ""
        if path:
            subprocess.Popen(["omarchy-launch-editor", path], start_new_session=True)
            return 0
    if event.action == "reminder":
        subprocess.run(["omarchy", "reminder", "show"], check=False)
        return 0
    if event.action == "calendar":
        subprocess.Popen(
            ["omarchy-shell", "shell", "toggle", "io.github.guiestrela.omarchy-google-calendar-clock"],
            start_new_session=True,
        )
        return 0
    notify("Index item", event.transcription[:200])
    return 0


def _resolve(event_id: str) -> Event | None:
    event = get_event(event_id)
    if event:
        return event
    matches = [item for item in list_events(200) if item.id.startswith(event_id)]
    return matches[0] if len(matches) == 1 else None
