from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .catalog import match_regex, match_wake, model_action_names, model_instructions
from .config import Config, ModelEndpoint

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
DATEISH = re.compile(
    r"\b("
    r"today|tomorrow|tonight|next\s+\w+|"
    + "|".join(WEEKDAYS)
    + r"|\d{1,2}:\d{2}|\d{1,2}\s*(am|pm)|at\s+\d{1,2}"
    r")\b",
    re.IGNORECASE,
)
RELATIVE = re.compile(
    r"\b(?:remind\s+me\s+)?in\s+(\d+)\s+(minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
FILLER = re.compile(r"^(?:hey|ok|okay|please|um|uh)[, ]+", re.IGNORECASE)
TITLE_STRIP = re.compile(
    r"^(?:remind\s+me\s+(?:to|that)\s+|note\s+(?:that\s+)?|remember\s+to\s+)",
    re.IGNORECASE,
)


@dataclass
class Action:
    name: str
    title: str
    minutes: int | None = None
    when: datetime | None = None
    body: str = ""
    prompt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def args(self) -> dict[str, Any]:
        payload = {"title": self.title, "body": self.body, "prompt": self.prompt}
        if self.minutes is not None:
            payload["minutes"] = self.minutes
        if self.when is not None:
            payload["when"] = self.when.isoformat()
        payload.update(self.extra)
        return payload


def classify(text: str, recorded_at: datetime, config: Config) -> Action:
    mode = config.classifier
    if mode == "rules":
        return classify_rules(text, recorded_at, config)
    endpoint = config.endpoint_for(mode)
    if not endpoint.configured():
        raise RuntimeError(f"{mode} classifier is selected but base_url/model are empty")
    return classify_http(text, recorded_at, endpoint, config)


def classify_rules(text: str, recorded_at: datetime, config: Config) -> Action:
    cleaned = FILLER.sub("", text.strip()).strip()
    catalog = config.actions()
    woken = match_wake(cleaned, catalog, config.wake_phrases)
    if woken is not None:
        spec, matched = woken
        remainder = cleaned[matched.end() :].strip() or cleaned
        if not spec.available(config.agent_enabled):
            fallback = catalog.default_id(config.agent_enabled)
            return Action(fallback, title=_title(cleaned), prompt=cleaned, body=text)
        return Action(spec.id, title=_title(remainder), prompt=remainder, body=text)

    reminder = catalog.get("reminder")
    calendar = catalog.get("calendar")
    relative = RELATIVE.search(cleaned)
    if relative and reminder is not None and reminder.enabled:
        count = int(relative.group(1))
        unit = relative.group(2).lower()
        minutes = count * 60 if unit.startswith("h") else count
        minutes = max(1, minutes)
        title = _title(RELATIVE.sub("", cleaned).strip() or cleaned)
        if minutes > 24 * 60 and calendar is not None and calendar.enabled:
            when = recorded_at + timedelta(minutes=minutes)
            return Action(calendar.id, title=title, when=when, body=text)
        return Action(reminder.id, title=title, minutes=minutes, body=text)

    when = parse_dateish(cleaned, recorded_at)
    if when is not None and calendar is not None and calendar.enabled:
        return Action(calendar.id, title=_title(cleaned), when=when, body=text)

    regex = match_regex(cleaned, catalog, config.agent_enabled)
    if regex is not None:
        return Action(regex.id, title=_title(cleaned), body=text)

    fallback = catalog.default_id(config.agent_enabled)
    return Action(fallback, title=_title(cleaned), prompt=cleaned, body=text)


def _title(text: str) -> str:
    title = TITLE_STRIP.sub("", text).strip() or text.strip() or "Index note"
    title = re.sub(r"\s+", " ", title)
    if len(title) > 120:
        title = title[:117] + "..."
    return title


def parse_dateish(text: str, recorded_at: datetime) -> datetime | None:
    match = DATEISH.search(text)
    if match is None:
        return None
    words = text[match.start() :].split()
    for length in range(min(6, len(words)), 0, -1):
        when = parse_local_datetime(" ".join(words[:length]), recorded_at)
        if when is not None:
            return when
    return None


def parse_local_datetime(text: str, recorded_at: datetime) -> datetime | None:
    """Parse a human date with GNU date. Returns None if date cannot interpret it."""
    try:
        result = subprocess.run(
            ["date", f"--date={text}", "+%Y-%m-%dT%H:%M:%S"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    stamp = result.stdout.strip()
    try:
        naive = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    tz = recorded_at.tzinfo or datetime.now().astimezone().tzinfo
    return naive.replace(tzinfo=tz)


def _message_text(payload: dict[str, Any]) -> str:
    message = payload["choices"][0]["message"]
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("classifier returned empty content")
    return content


def parse_model_json(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start : end + 1])
        if isinstance(data, dict):
            return data
    raise RuntimeError("classifier returned unusable JSON")


def classify_http(text: str, recorded_at: datetime, endpoint: ModelEndpoint, config: Config) -> Action:
    catalog = config.actions()
    names = model_action_names(catalog, config.agent_enabled)
    schema = {
        "action": "|".join(names),
        "title": "short title",
        "minutes": "integer minutes from now for reminder, or null",
        "when": "RFC3339 local datetime for calendar, or null",
        "body": "full text",
        "prompt": "agent or herdr prompt",
    }
    body = {
        "model": endpoint.model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Classify a Pebble Index voice transcript into exactly one action. "
                    "Return only JSON matching this schema: "
                    f"{json.dumps(schema)}. "
                    "Choose from these actions:\n"
                    f"{model_instructions(catalog, config.agent_enabled)}\n"
                    f"agent_enabled={str(config.agent_enabled).lower()}. "
                    "Never invent a time."
                ),
            },
            {
                "role": "user",
                "content": f"recorded_at={recorded_at.isoformat()}\n{text}",
            },
        ],
    }
    url = endpoint.base_url.rstrip("/") + "/v1/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Goooooooooody/omarchy-pebble-index",
            "X-Title": "omarchy-pebble-index",
        },
    )
    if endpoint.api_key:
        request.add_header("Authorization", f"Bearer {endpoint.api_key}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"classifier HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"classifier HTTP failed: {error}") from error
    try:
        data = parse_model_json(_message_text(payload))
    except (KeyError, IndexError, TypeError, RuntimeError) as error:
        raise RuntimeError(f"classifier returned unusable JSON: {error}") from error
    return action_from_model(data, text, recorded_at, config)


def action_from_model(data: dict[str, Any], text: str, recorded_at: datetime, config: Config) -> Action:
    catalog = config.actions()
    name = str(data.get("action") or catalog.default_id(config.agent_enabled)).strip().lower()
    spec = catalog.get(name)
    if spec is None or not spec.available(config.agent_enabled):
        name = catalog.default_id(config.agent_enabled)
        spec = catalog.get(name)
    title = _title(str(data.get("title") or text))
    body = str(data.get("body") or text)
    prompt = str(data.get("prompt") or title)
    minutes = data.get("minutes")
    when_raw = data.get("when")
    when = None
    if when_raw:
        try:
            when = datetime.fromisoformat(str(when_raw))
            if when.tzinfo is None:
                when = when.replace(tzinfo=recorded_at.tzinfo)
        except ValueError:
            when = None
    extra = {
        key: value
        for key, value in data.items()
        if key not in {"action", "title", "minutes", "when", "body", "prompt"}
    }
    builtin = spec.builtin if spec else ""
    if builtin == "reminder" or name == "reminder":
        try:
            parsed_minutes = int(minutes)
        except (TypeError, ValueError):
            return Action(
                catalog.default_id(config.agent_enabled),
                title=title,
                prompt=prompt,
                body=body,
                extra=extra,
            )
        parsed_minutes = max(1, parsed_minutes)
        if parsed_minutes > 24 * 60:
            return Action(
                "calendar",
                title=title,
                when=recorded_at + timedelta(minutes=parsed_minutes),
                body=body,
                extra=extra,
            )
        return Action("reminder", title=title, minutes=parsed_minutes, body=body, extra=extra)
    if builtin == "calendar" or name == "calendar":
        if when is None:
            return Action(
                catalog.default_id(config.agent_enabled),
                title=title,
                prompt=prompt,
                body=body,
                extra=extra,
            )
        return Action("calendar", title=title, when=when, body=body, extra=extra)
    if builtin == "herdr" or name == "herdr":
        return Action("herdr", title=title, prompt=prompt, body=body, extra=extra)
    if builtin == "agent" or name == "agent":
        return Action("agent", title=title, prompt=prompt or body, body=body, extra=extra)
    return Action(name, title=title, prompt=prompt, body=body, extra=extra)


def local_timezone() -> ZoneInfo | timezone:
    tz = datetime.now().astimezone().tzinfo
    return tz or timezone.utc


def recorded_at_from_ms(value: str) -> datetime:
    try:
        millis = int(value)
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).astimezone(local_timezone())
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(local_timezone())
