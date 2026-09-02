from __future__ import annotations

import subprocess


def notify(headline: str, description: str = "", *, critical: bool = False) -> None:
    urgency = "critical" if critical else "normal"
    command = [
        "omarchy-notification-send",
        "--app-name",
        "Pebble Index",
        "-g",
        "󰻃",
        "-u",
        urgency,
        headline,
    ]
    if description:
        command.append(description)
    try:
        subprocess.run(command, check=False, timeout=5, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        fallback = ["notify-send", "-u", urgency, headline]
        if description:
            fallback.append(description)
        try:
            subprocess.run(fallback, check=False, timeout=5, capture_output=True)
        except (OSError, subprocess.TimeoutExpired):
            pass
