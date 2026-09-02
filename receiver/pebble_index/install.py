from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .bind import BindError, tailscale_ipv4
from .paths import APP_NAME, PLUGIN_ID, plugin_root

UNIT_NAME = f"{APP_NAME}.service"
REQUIRED_TOOLS = ("tailscale", "systemctl")
_TOKEN_LINE = re.compile(r'(?m)^token = ".*"$')
PLACEHOLDER_TOKEN = "CHANGE_ME"

Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
AddressFn = Callable[[], str]


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    home: Path
    plugin_root: Path

    @property
    def plugin_home(self) -> Path:
        return self.home / ".config" / "omarchy" / "plugins" / PLUGIN_ID

    @property
    def config_dir(self) -> Path:
        return self.home / ".config" / APP_NAME

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def env_file(self) -> Path:
        return self.config_dir / ".env"

    @property
    def user_actions(self) -> Path:
        return self.config_dir / "actions"

    @property
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / APP_NAME

    @property
    def data_dir(self) -> Path:
        return self.home / ".local" / "share" / APP_NAME

    @property
    def notes_inbox(self) -> Path:
        return self.home / "Notes" / "inbox"

    @property
    def unit_dir(self) -> Path:
        return self.home / ".config" / "systemd" / "user"

    @property
    def unit_file(self) -> Path:
        return self.unit_dir / UNIT_NAME

    @property
    def bin_link(self) -> Path:
        return self.home / ".local" / "bin" / "pebble-index"

    @property
    def old_plugin_home(self) -> Path:
        return self.home / ".config" / "omarchy" / "plugins" / "io.github.goody.omarchy-pebble-index"


def default_layout() -> Layout:
    return Layout(Path.home(), plugin_root())


def missing_tools(which: Which = shutil.which) -> list[str]:
    return [name for name in REQUIRED_TOOLS if which(name) is None]


def read_token(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return str(data.get("token") or "").strip()


def is_provisioned(path: Path | None = None) -> bool:
    token = read_token(path if path is not None else default_layout().config_file)
    return bool(token) and token != PLACEHOLDER_TOKEN


def webhook_payload(
    layout: Layout | None = None,
    *,
    address_fn: AddressFn = tailscale_ipv4,
) -> dict[str, Any]:
    target = layout or default_layout()
    token = read_token(target.config_file)
    port = 8787
    if target.config_file.is_file():
        try:
            data = tomllib.loads(target.config_file.read_text(encoding="utf-8"))
            port = int(data.get("bind_port") or 8787)
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            port = 8787
    provisioned = bool(token) and token != PLACEHOLDER_TOKEN
    address_error = ""
    address = ""
    try:
        address = address_fn()
    except BindError as exc:
        address_error = str(exc)
    except OSError as exc:
        address_error = str(exc)
    error = "" if provisioned else "receiver is not set up"
    if provisioned and address_error:
        error = address_error
    host = address or "<tailscale-ip>"
    url = f"http://{host}:{port}/webhook"
    return {
        "ok": provisioned and not address_error,
        "provisioned": provisioned,
        "url": url,
        "token": token if provisioned else "",
        "authorization": f"Bearer {token}" if provisioned else "",
        "address": address,
        "port": port,
        "error": error,
    }


def provision(
    layout: Layout | None = None,
    *,
    start_unit: bool = True,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    address_fn: AddressFn = tailscale_ipv4,
) -> dict[str, Any]:
    target = layout or default_layout()
    missing = missing_tools(which)
    if missing:
        raise InstallError("Missing required tool: " + ", ".join(missing))
    if not (target.plugin_root / "manifest.json").is_file():
        raise InstallError("setup must be run from the plugin checkout")

    _remove_old_plugin_link(target)
    _link_plugin(target)
    for directory in (
        target.config_dir,
        target.user_actions,
        target.state_dir,
        target.data_dir,
        target.notes_inbox,
        target.unit_dir,
        target.bin_link.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)

    _ensure_env(target)
    token = _ensure_token(target)
    _install_unit(target)
    _link_bin(target)
    started = False
    if start_unit:
        _systemctl(runner, ["daemon-reload"])
        _systemctl(runner, ["enable", "--now", UNIT_NAME])
        started = True

    payload = webhook_payload(target, address_fn=address_fn)
    payload["token"] = token
    payload["authorization"] = f"Bearer {token}"
    payload["provisioned"] = True
    payload["started"] = started
    payload["ok"] = True
    if payload.get("error"):
        payload["ok"] = False
    return payload


def teardown(
    layout: Layout | None = None,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    target = layout or default_layout()
    try:
        runner(
            ["systemctl", "--user", "disable", "--now", UNIT_NAME],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        pass
    if target.unit_file.is_file() or target.unit_file.is_symlink():
        target.unit_file.unlink()
    try:
        _systemctl(runner, ["daemon-reload"])
    except (InstallError, OSError):
        pass
    if target.bin_link.is_symlink() or target.bin_link.is_file():
        target.bin_link.unlink()
    return {"ok": True, "stopped": True, "provisioned": is_provisioned(target.config_file)}


def copy_field(
    field: str,
    layout: Layout | None = None,
    *,
    runner: Runner = subprocess.run,
    address_fn: AddressFn = tailscale_ipv4,
) -> dict[str, Any]:
    if field not in {"url", "token", "authorization"}:
        raise InstallError("copy field must be url, token, or authorization")
    payload = webhook_payload(layout, address_fn=address_fn)
    if not payload["provisioned"]:
        raise InstallError("receiver is not set up")
    text = str(payload[field])
    if not text:
        raise InstallError(f"{field} is empty")
    try:
        result = runner(
            ["wl-copy"],
            input=text,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise InstallError(f"wl-copy failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "wl-copy failed").strip()
        raise InstallError(detail)
    return {"ok": True, "copied": field}


def format_setup(payload: dict[str, Any]) -> str:
    return (
        "\nPebble Index receiver is installed.\n"
        "\n"
        "CoreApp → Index → Webhook:\n"
        f"  URL:    {payload['url']}\n"
        "  Header: Authorization\n"
        f"  Value:  {payload['authorization']}\n"
        "  Send:   Transcription\n"
        "  Trigger: whichever gesture you want\n"
        "\n"
        "Unclassified transcripts open omarchy agent. Say “herdr …” for Herdr.\n"
        "Set agent_enabled = false to fall back to notes.\n"
        "\n"
        "Open the bar widget to copy these again. Stop the receiver from there,\n"
        "or run: pebble-index uninstall\n"
        "Menu-only plugin removal leaves this systemd unit running until Stop\n"
        "receiver, or until the unit notices the plugin directory is gone.\n"
    )


def format_teardown() -> str:
    return (
        f"Stopped {UNIT_NAME}.\n"
        "Left config, inbox, and sqlite in place:\n"
        "  ~/.config/omarchy-pebble-index\n"
        "  ~/.local/share/omarchy-pebble-index\n"
        "  ~/.local/state/omarchy-pebble-index\n"
        "  ~/Notes/inbox\n"
        "\n"
        "Omarchy plugin files (if you used omarchy plugin add) are separate:\n"
        f"  omarchy plugin remove {PLUGIN_ID}\n"
    )


def _remove_old_plugin_link(layout: Layout) -> None:
    old = layout.old_plugin_home
    if old.is_symlink():
        old.unlink()


def _link_plugin(layout: Layout) -> None:
    source = layout.plugin_root.resolve()
    dest = layout.plugin_home
    dest_resolved = dest.resolve() if dest.exists() or dest.is_symlink() else dest
    if source == dest_resolved or source == dest:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not dest.is_symlink():
        raise InstallError(
            f"Plugin directory already exists at {dest}. "
            "Remove it or run setup from that checkout."
        )
    if dest.is_symlink():
        dest.unlink()
    dest.symlink_to(source)


def _ensure_env(layout: Layout) -> None:
    if layout.env_file.is_file():
        layout.env_file.chmod(0o600)
        return
    example = layout.plugin_root / ".env.example"
    if example.is_file():
        shutil.copyfile(example, layout.env_file)
    else:
        layout.env_file.write_text("# OPENROUTER_API_KEY=\n", encoding="utf-8")
    layout.env_file.chmod(0o600)


def _ensure_token(layout: Layout) -> str:
    example = layout.plugin_root / "config.example.toml"
    if not layout.config_file.is_file():
        if not example.is_file():
            raise InstallError("missing config.example.toml")
        token = secrets.token_hex(32)
        text = example.read_text(encoding="utf-8").replace(PLACEHOLDER_TOKEN, token, 1)
        _write_secret(layout.config_file, text)
        return token
    token = read_token(layout.config_file)
    if token and token != PLACEHOLDER_TOKEN:
        layout.config_file.chmod(0o600)
        return token
    token = secrets.token_hex(32)
    text = layout.config_file.read_text(encoding="utf-8")
    if _TOKEN_LINE.search(text):
        text = _TOKEN_LINE.sub(f'token = "{token}"', text, count=1)
    else:
        text = text.rstrip() + f'\n\ntoken = "{token}"\n'
    _write_secret(layout.config_file, text)
    return token


def _install_unit(layout: Layout) -> None:
    source = layout.plugin_root / "systemd" / UNIT_NAME
    if not source.is_file():
        raise InstallError(f"missing {source}")
    shutil.copyfile(source, layout.unit_file)
    layout.unit_file.chmod(0o644)


def _link_bin(layout: Layout) -> None:
    binary = layout.plugin_home / "bin" / "pebble-index"
    if not binary.is_file() and not binary.is_symlink():
        binary = layout.plugin_root / "bin" / "pebble-index"
    if binary.is_file():
        binary.chmod(binary.stat().st_mode | 0o111)
    if layout.bin_link.is_symlink() or layout.bin_link.exists():
        layout.bin_link.unlink()
    layout.bin_link.symlink_to(binary)


def _write_secret(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _systemctl(runner: Runner, args: list[str]) -> None:
    command = ["systemctl", "--user", *args]
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise InstallError(f"systemctl failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl failed").strip()
        raise InstallError(detail)
