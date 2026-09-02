from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[..., subprocess.CompletedProcess[str]]


def capture_active_window(dest: Path, *, run: Runner | None = None) -> Path | None:
    """Screenshot the focused Hyprland window. Returns None if capture fails."""
    runner = run or subprocess.run
    dest.parent.mkdir(parents=True, exist_ok=True)
    geometry = _window_geometry(runner) or _monitor_geometry(runner)
    if not geometry:
        return None
    try:
        result = runner(
            ["grim", "-g", geometry, str(dest)],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        return None
    dest.chmod(0o600)
    return dest


def _window_geometry(run: Runner) -> str:
    data = _hypr_json(run, "activewindow")
    if not isinstance(data, dict):
        return ""
    at = data.get("at")
    size = data.get("size")
    if not isinstance(at, list) or not isinstance(size, list) or len(at) < 2 or len(size) < 2:
        return ""
    try:
        x, y = int(at[0]), int(at[1])
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError):
        return ""
    if width < 8 or height < 8:
        return ""
    return f"{x},{y} {width}x{height}"


def _monitor_geometry(run: Runner) -> str:
    data = _hypr_json(run, "monitors")
    if not isinstance(data, list):
        return ""
    focused = next((item for item in data if isinstance(item, dict) and item.get("focused")), None)
    if focused is None:
        return ""
    try:
        x = int(focused.get("x", 0))
        y = int(focused.get("y", 0))
        width = int(int(focused.get("width", 0)) / float(focused.get("scale") or 1))
        height = int(int(focused.get("height", 0)) / float(focused.get("scale") or 1))
    except (TypeError, ValueError):
        return ""
    if width < 8 or height < 8:
        return ""
    return f"{x},{y} {width}x{height}"


def _hypr_json(run: Runner, topic: str) -> object:
    try:
        result = run(
            ["hyprctl", topic, "-j"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
