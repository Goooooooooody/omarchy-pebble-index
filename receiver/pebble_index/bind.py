from __future__ import annotations

import ipaddress
import subprocess

TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


class BindError(Exception):
    pass


def require_cgnat_ipv4(address: str) -> str:
    """Accept only a Tailscale CGNAT IPv4. Refuses 0.0.0.0 and LAN/public addresses."""
    text = address.strip()
    if not text:
        raise BindError("tailscale returned no IPv4 address")
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError as error:
        raise BindError(f"invalid tailscale address {text}") from error
    if parsed not in TAILSCALE_NET:
        raise BindError(f"{text} is not a Tailscale CGNAT address")
    return text


def tailscale_ipv4() -> str:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BindError(f"tailscale ip failed: {error}") from error
    if result.returncode != 0:
        raise BindError((result.stderr or result.stdout or "tailscale ip failed").strip())
    address = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
    return require_cgnat_ipv4(address)
