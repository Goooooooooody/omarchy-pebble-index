from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .paths import action_search_paths

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
BUILTIN_IDS = ("note", "reminder", "calendar", "herdr", "agent")
MATCH_KINDS = ("wake", "relative", "dateish", "regex", "default")
CONTEXT_KINDS = ("active-window",)


@dataclass
class ActionSpec:
    id: str
    label: str
    description: str
    enabled: bool = True
    priority: int = 80
    builtin: str = ""
    default: bool = False
    match: str = "wake"
    wake: list[str] = field(default_factory=list)
    pattern: str = ""
    fields: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    timeout: int = 15
    background: bool = False
    fallback: str = "note"
    require: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    glyph: str = ""
    source: Path | None = None

    def available(self, agent_enabled: bool) -> bool:
        if not self.enabled:
            return False
        if "agent_enabled" in self.require and not agent_enabled:
            return False
        return True


@dataclass
class Catalog:
    specs: dict[str, ActionSpec]

    def get(self, name: str) -> ActionSpec | None:
        return self.specs.get(name)

    def ids(self) -> set[str]:
        return set(self.specs)

    def all(self) -> list[ActionSpec]:
        return sorted(self.specs.values(), key=lambda spec: (-spec.priority, spec.id))

    def default_id(self, agent_enabled: bool = True) -> str:
        for spec in self.all():
            if spec.default and spec.available(agent_enabled):
                return spec.id
        for spec in self.all():
            if spec.builtin == "note" and spec.enabled:
                return spec.id
        return "note"

    def wakes(self, spec: ActionSpec, extra: list[str] | None = None) -> list[str]:
        phrases = list(spec.wake)
        if spec.builtin == "herdr" and extra:
            phrases.extend(extra)
        seen: set[str] = set()
        ordered: list[str] = []
        for phrase in phrases:
            key = phrase.strip().lower()
            if key and key not in seen:
                seen.add(key)
                ordered.append(phrase.strip())
        return ordered

    def apply_overrides(self, overrides: dict[str, dict]) -> Catalog:
        specs = {key: replace(spec) for key, spec in self.specs.items()}
        for name, values in overrides.items():
            spec = specs.get(name)
            if spec is None or not isinstance(values, dict):
                continue
            if "enabled" in values:
                spec.enabled = bool(values["enabled"])
        return Catalog(specs)


def builtin_specs() -> list[ActionSpec]:
    return [
        ActionSpec(
            id="herdr",
            label="Herdr",
            description="Send the rest of the transcript to Herdr. Only if it starts with herd or herdr.",
            priority=100,
            builtin="herdr",
            match="wake",
            wake=["herd", "herdr"],
            fields=["title", "prompt"],
            require=["agent_enabled"],
            glyph="󰚩",
        ),
        ActionSpec(
            id="agent",
            label="Agent",
            description="Open omarchy agent with the transcript. Default when nothing else matches.",
            priority=10,
            builtin="agent",
            default=True,
            match="default",
            fields=["title", "prompt"],
            require=["agent_enabled"],
            glyph="󰀎",
        ),
        ActionSpec(
            id="reminder",
            label="Reminder",
            description="Relative delay of a day or less (in N minutes/hours).",
            priority=50,
            builtin="reminder",
            match="relative",
            fields=["title", "minutes"],
            glyph="󰢌",
        ),
        ActionSpec(
            id="calendar",
            label="Calendar",
            description="Dated event (tomorrow 3pm, Friday at 2, in 2 days, September 5th at 3).",
            priority=40,
            builtin="calendar",
            match="dateish",
            fields=["title", "when"],
            glyph="󰃭",
        ),
        ActionSpec(
            id="note",
            label="Note",
            description="Inbox markdown note. Use when the transcript starts with note.",
            priority=0,
            builtin="note",
            match="wake",
            wake=["note"],
            fields=["title", "body"],
            glyph="󰎞",
        ),
    ]


def builtin_catalog() -> Catalog:
    return Catalog({spec.id: spec for spec in builtin_specs()})


def load_catalog(paths: list[Path] | None = None, overrides: dict[str, dict] | None = None) -> Catalog:
    specs = {spec.id: spec for spec in builtin_specs()}
    for directory in paths if paths is not None else action_search_paths():
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob("*.toml")):
            spec = spec_from_toml(file)
            specs[spec.id] = spec
    catalog = Catalog(specs)
    if overrides:
        catalog = catalog.apply_overrides(overrides)
    return catalog


def spec_from_toml(path: Path) -> ActionSpec:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    stem = path.stem.lower()
    ident = str(data.get("id") or stem).strip().lower()
    if not ID_RE.match(ident):
        raise ValueError(f"{path}: id must match {ID_RE.pattern}")
    match = str(data.get("match") or ("wake" if data.get("wake") else "default")).strip().lower()
    if match not in MATCH_KINDS:
        raise ValueError(f"{path}: match must be one of {', '.join(MATCH_KINDS)}")
    builtin = str(data.get("builtin") or "").strip().lower()
    if builtin and builtin not in BUILTIN_IDS:
        raise ValueError(f"{path}: builtin must be one of {', '.join(BUILTIN_IDS)}")
    command = data.get("command") or []
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError(f"{path}: command must be a list of strings")
    wake = _string_list(data.get("wake"), f"{path}: wake")
    fields = _string_list(data.get("fields"), f"{path}: fields")
    require = _string_list(data.get("require"), f"{path}: require")
    context = _string_list(data.get("context"), f"{path}: context")
    unknown = [item for item in context if item not in CONTEXT_KINDS]
    if unknown:
        raise ValueError(f"{path}: context must be one of {', '.join(CONTEXT_KINDS)}")
    fallback = str(data.get("fallback") or "note").strip().lower()
    if fallback not in {"note", "none"}:
        raise ValueError(f"{path}: fallback must be note or none")
    return ActionSpec(
        id=ident,
        label=str(data.get("label") or ident.replace("-", " ").title()),
        description=str(data.get("description") or "").strip(),
        enabled=bool(data.get("enabled", True)),
        priority=int(data.get("priority") or 80),
        builtin=builtin,
        default=bool(data.get("default", False)),
        match=match,
        wake=wake,
        pattern=str(data.get("pattern") or ""),
        fields=fields or ["title", "body"],
        command=list(command),
        timeout=max(1, int(data.get("timeout") or 15)),
        background=bool(data.get("background", False)),
        fallback=fallback,
        require=require,
        context=context,
        glyph=str(data.get("glyph") or ""),
        source=path,
    )


def _string_list(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
        raise ValueError(f"{label} must be a list of strings")
    return [part.strip() for part in value if part.strip()]


def model_action_names(catalog: Catalog, agent_enabled: bool) -> list[str]:
    names: list[str] = []
    for spec in catalog.all():
        if spec.available(agent_enabled):
            names.append(spec.id)
    return names


def match_wake(text: str, catalog: Catalog, extra_herdr: list[str] | None = None) -> tuple[ActionSpec, re.Match[str]] | None:
    best: tuple[int, int, ActionSpec, re.Match[str]] | None = None
    for spec in catalog.all():
        if not spec.enabled:
            continue
        extra = extra_herdr if spec.builtin == "herdr" else None
        phrases = catalog.wakes(spec, extra)
        if not phrases:
            continue
        for phrase in phrases:
            pattern = r"^(?:hey[, ]+)?" + re.escape(phrase) + r"[,.]?\s+"
            found = re.match(pattern, text, flags=re.IGNORECASE)
            if found is None:
                continue
            score = (spec.priority, len(phrase))
            if best is None or score > (best[0], best[1]):
                best = (spec.priority, len(phrase), spec, found)
    if best is None:
        return None
    return best[2], best[3]


def match_regex(text: str, catalog: Catalog, agent_enabled: bool) -> ActionSpec | None:
    for spec in catalog.all():
        if spec.match != "regex" or not spec.pattern or not spec.available(agent_enabled):
            continue
        try:
            if re.search(spec.pattern, text):
                return spec
        except re.error:
            continue
    return None


def model_instructions(catalog: Catalog, agent_enabled: bool) -> str:
    lines = []
    for spec in catalog.all():
        if not spec.available(agent_enabled):
            continue
        fields = ", ".join(spec.fields) if spec.fields else "title"
        extra = spec.description or spec.label
        lines.append(f"- {spec.id}: {extra} fields=[{fields}]")
    return "\n".join(lines)
