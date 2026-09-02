from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from pebble_index.bind import BindError
from pebble_index.install import (
    PLACEHOLDER_TOKEN,
    UNIT_NAME,
    InstallError,
    Layout,
    copy_field,
    is_provisioned,
    missing_tools,
    provision,
    read_token,
    teardown,
    webhook_payload,
)
from pebble_index.paths import PLUGIN_ID


def _write_plugin(root: Path) -> None:
    (root / "bin").mkdir(parents=True)
    (root / "systemd").mkdir()
    (root / "bin" / "pebble-index").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "manifest.json").write_text('{"id":"test"}\n', encoding="utf-8")
    (root / "config.example.toml").write_text(
        'bind_port = 8787\nclassifier = "rules"\ntoken = "CHANGE_ME"\n',
        encoding="utf-8",
    )
    (root / ".env.example").write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")
    (root / "systemd" / UNIT_NAME).write_text(
        "[Unit]\nConditionPathExists=%h/.config/omarchy/plugins/"
        f"{PLUGIN_ID}/manifest.json\n",
        encoding="utf-8",
    )


class Recorder:
    def __init__(self, fail: set[tuple[str, ...]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.fail = fail or set()

    def __call__(self, args, **kwargs):
        command = [str(part) for part in args]
        self.calls.append(command)
        self.inputs.append(kwargs.get("input"))
        if tuple(command) in self.fail:
            return subprocess.CompletedProcess(command, 1, "", "nope")
        return subprocess.CompletedProcess(command, 0, "", "")


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.plugin = self.root / "checkout"
        _write_plugin(self.plugin)
        self.layout = Layout(self.home, self.plugin)
        self.which = lambda name: f"/bin/{name}"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_tailscale_is_reported(self) -> None:
        self.assertEqual(missing_tools(lambda name: None if name == "tailscale" else f"/bin/{name}"), ["tailscale"])

    def test_provision_writes_secret_config_and_does_not_start_without_flag(self) -> None:
        runner = Recorder()
        payload = provision(
            self.layout,
            start_unit=False,
            runner=runner,
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        token = read_token(self.layout.config_file)
        self.assertTrue(is_provisioned(self.layout.config_file))
        self.assertNotEqual(token, PLACEHOLDER_TOKEN)
        self.assertEqual(len(token), 64)
        self.assertEqual(self.layout.config_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.layout.env_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.layout.notes_inbox.stat().st_mode & 0o777, 0o700)
        self.assertTrue(self.layout.unit_file.is_file())
        self.assertIn("ConditionPathExists", self.layout.unit_file.read_text(encoding="utf-8"))
        self.assertTrue(self.layout.bin_link.is_symlink())
        self.assertTrue(self.layout.plugin_home.is_symlink())
        self.assertEqual(payload["url"], "http://100.64.1.8:8787/webhook")
        self.assertEqual(payload["authorization"], f"Bearer {token}")
        self.assertFalse(payload["started"])
        self.assertEqual(runner.calls, [])

    def test_omarchy_add_checkout_is_not_replaced_with_a_symlink(self) -> None:
        plugin_home = self.layout.plugin_home
        plugin_home.parent.mkdir(parents=True)
        _write_plugin(plugin_home)
        layout = Layout(self.home, plugin_home)
        provision(
            layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        self.assertTrue(plugin_home.is_dir())
        self.assertFalse(plugin_home.is_symlink())
        self.assertTrue(is_provisioned(layout.config_file))

    def test_existing_token_is_kept(self) -> None:
        provision(
            self.layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        token = read_token(self.layout.config_file)
        self.layout.env_file.write_text("OPENROUTER_API_KEY=keep-me\n", encoding="utf-8")
        again = provision(
            self.layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        self.assertEqual(read_token(self.layout.config_file), token)
        self.assertEqual(again["token"], token)
        self.assertEqual(self.layout.env_file.read_text(encoding="utf-8"), "OPENROUTER_API_KEY=keep-me\n")

    def test_placeholder_token_is_replaced(self) -> None:
        self.layout.config_dir.mkdir(parents=True)
        self.layout.config_file.write_text('token = "CHANGE_ME"\n', encoding="utf-8")
        provision(
            self.layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        token = read_token(self.layout.config_file)
        self.assertTrue(token)
        self.assertNotEqual(token, PLACEHOLDER_TOKEN)

    def test_start_unit_runs_systemctl(self) -> None:
        runner = Recorder()
        payload = provision(
            self.layout,
            start_unit=True,
            runner=runner,
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        self.assertTrue(payload["started"])
        self.assertIn(["systemctl", "--user", "daemon-reload"], runner.calls)
        self.assertIn(["systemctl", "--user", "enable", "--now", UNIT_NAME], runner.calls)

    def test_missing_tool_stops_before_write(self) -> None:
        with self.assertRaises(InstallError):
            provision(
                self.layout,
                start_unit=False,
                runner=Recorder(),
                which=lambda name: None,
                address_fn=lambda: "100.64.1.8",
            )
        self.assertFalse(self.layout.config_file.exists())

    def test_webhook_without_config(self) -> None:
        payload = webhook_payload(self.layout, address_fn=lambda: "100.64.1.8")
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["provisioned"])
        self.assertEqual(payload["token"], "")
        self.assertEqual(payload["error"], "receiver is not set up")

    def test_webhook_reports_tailscale_failure_without_dropping_token(self) -> None:
        provision(
            self.layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        token = read_token(self.layout.config_file)

        def boom() -> str:
            raise BindError("tailscale is down")

        payload = webhook_payload(self.layout, address_fn=boom)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["provisioned"])
        self.assertEqual(payload["token"], token)
        self.assertEqual(payload["error"], "tailscale is down")
        self.assertIn("<tailscale-ip>", payload["url"])

    def test_teardown_removes_unit_and_keeps_config(self) -> None:
        provision(
            self.layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        runner = Recorder()
        result = teardown(self.layout, runner=runner)
        self.assertTrue(result["ok"])
        self.assertTrue(result["provisioned"])
        self.assertFalse(self.layout.unit_file.exists())
        self.assertFalse(self.layout.bin_link.exists())
        self.assertTrue(self.layout.config_file.is_file())
        self.assertIn(["systemctl", "--user", "disable", "--now", UNIT_NAME], runner.calls)

    def test_copy_pipes_token_to_wl_copy(self) -> None:
        provision(
            self.layout,
            start_unit=False,
            runner=Recorder(),
            which=self.which,
            address_fn=lambda: "100.64.1.8",
        )
        token = read_token(self.layout.config_file)
        runner = Recorder()
        payload = copy_field("token", self.layout, runner=runner, address_fn=lambda: "100.64.1.8")
        self.assertEqual(payload, {"ok": True, "copied": "token"})
        self.assertEqual(runner.calls, [["wl-copy"]])
        self.assertEqual(runner.inputs, [token])

    def test_packaged_unit_requires_plugin_manifest(self) -> None:
        unit = Path(__file__).resolve().parents[1] / "systemd" / UNIT_NAME
        text = unit.read_text(encoding="utf-8")
        self.assertIn(
            f"ConditionPathExists=%h/.config/omarchy/plugins/{PLUGIN_ID}/manifest.json",
            text,
        )


if __name__ == "__main__":
    unittest.main()
