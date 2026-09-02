from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .catalog import ActionSpec
from .classify import Action
from .config import Config


CALENDAR_CREATE_CANDIDATES = (
    Path.home()
    / ".config/omarchy/plugins/io.github.guiestrela.omarchy-google-calendar-clock/scripts/calendar-create",
)
CALDIR_CANDIDATES = (
    Path.home() / ".local/share/io.github.guiestrela.omarchy-google-calendar-clock/bin/caldir",
)


class DispatchError(Exception):
    def __init__(self, message: str, fallback_note: bool = False):
        super().__init__(message)
        self.fallback_note = fallback_note


def dispatch(action: Action, config: Config, event_id: str) -> str:
    spec = config.actions().get(action.name)
    if spec is None:
        raise DispatchError(f"unknown action {action.name}")
    builtin = spec.builtin or (action.name if action.name in {"note", "reminder", "calendar", "herdr"} else "")
    if builtin == "note":
        return write_note(action, config, event_id)
    if builtin == "reminder":
        return set_reminder(action)
    if builtin == "calendar":
        try:
            return create_calendar(action)
        except DispatchError as error:
            if error.fallback_note:
                note_action = Action("note", title=f"{action.title} ({_when_label(action)})", body=action.body)
                path = write_note(note_action, config, event_id)
                return f"calendar-failed-note:{path}"
            raise
    if builtin == "herdr":
        return spawn_agent(action, config)
    if spec.command:
        try:
            return run_command(spec, action, event_id)
        except DispatchError as error:
            if spec.fallback == "note":
                path = write_note(Action("note", title=action.title, body=action.body), config, event_id)
                return f"{action.name}-failed-note:{path}"
            raise
    raise DispatchError(f"action {action.name} has no handler")


def run_command(spec: ActionSpec, action: Action, event_id: str) -> str:
    command = [_expand(part, spec, action, event_id) for part in spec.command]
    if not command:
        raise DispatchError(f"action {spec.id} command is empty")
    if spec.background:
        try:
            subprocess.Popen(
                command,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(spec.source.parent) if spec.source else None,
            )
        except OSError as error:
            raise DispatchError(str(error)) from error
        return f"{spec.id}:{command[0]}"
    result = subprocess.run(
        command,
        check=False,
        timeout=spec.timeout,
        capture_output=True,
        text=True,
        cwd=str(spec.source.parent) if spec.source else None,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        raise DispatchError(detail[:300], fallback_note=True)
    summary = (result.stdout or "").strip().splitlines()
    return summary[0] if summary else f"{spec.id}:{action.title}"


def _expand(part: str, spec: ActionSpec, action: Action, event_id: str) -> str:
    when = action.when.astimezone().isoformat() if action.when else ""
    values = {
        "{title}": action.title,
        "{body}": action.body,
        "{prompt}": action.prompt or action.title,
        "{minutes}": "" if action.minutes is None else str(action.minutes),
        "{when}": when,
        "{id}": event_id,
        "{text}": action.body or action.title,
        "{dir}": str(spec.source.parent) if spec.source else "",
    }
    rendered = part
    for key, value in values.items():
        rendered = rendered.replace(key, value)
    return rendered


def write_note(action: Action, config: Config, event_id: str) -> str:
    inbox = config.notes_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    inbox.chmod(0o700)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = inbox / f"{stamp}-{event_id[:8]}.md"
    recorded = action.extra.get("recorded_at", "")
    body = "\n".join(
        [
            "---",
            f"id: {event_id}",
            f"title: {action.title}",
            f"recordedAt: {recorded}",
            "client: ring",
            "---",
            "",
            action.body.strip() or action.title,
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def set_reminder(action: Action) -> str:
    minutes = max(1, int(action.minutes or 1))
    command = ["omarchy", "reminder", str(minutes), action.title]
    _run(command, timeout=10)
    return f"reminder:{minutes}m:{action.title}"


def create_calendar(action: Action) -> str:
    if action.when is None:
        raise DispatchError("calendar action missing when", fallback_note=True)
    local = action.when.astimezone()
    date = local.strftime("%Y-%m-%d")
    time_text = local.strftime("%H:%M")
    title = action.title.replace("\n", " ").replace("\r", " ").strip()
    create = next((path for path in CALENDAR_CREATE_CANDIDATES if os.access(path, os.X_OK)), None)
    if create is not None:
        _run(
            [str(create), title, date, time_text, "none", "", action.body[:10000]],
            timeout=15,
        )
        return f"calendar-create:{date}T{time_text}:{title}"

    caldir = shutil.which("caldir")
    if caldir is None:
        bundled = next((path for path in CALDIR_CANDIDATES if os.access(path, os.X_OK)), None)
        caldir = str(bundled) if bundled else None
    if caldir is None:
        raise DispatchError("caldir not installed", fallback_note=True)
    start = f"{date}T{time_text}"
    _run([caldir, "new", title, "--start", start, "--duration", "1h"], timeout=15)
    return f"caldir:{start}:{title}"


def spawn_agent(action: Action, config: Config) -> str:
    if not config.agent_enabled:
        raise DispatchError("agent sink is disabled")
    prompt = action.prompt or action.title
    command = [part.replace("{prompt}", prompt) for part in config.herdr_command]
    if not command:
        raise DispatchError("herdr_command is empty")
    # omarchy-agent execs a terminal and does not return; do not wait on it.
    try:
        subprocess.Popen(
            command,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path.home() / "Work") if (Path.home() / "Work").is_dir() else None,
        )
    except OSError as error:
        raise DispatchError(str(error)) from error
    return f"agent:{command[0]}"


def _run(command: list[str], timeout: int) -> None:
    result = subprocess.run(
        command,
        check=False,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        raise DispatchError(detail[:300], fallback_note=True)


def _when_label(action: Action) -> str:
    if action.when is None:
        return "unparsed-time"
    return action.when.astimezone().strftime("%Y-%m-%d %H:%M")
