from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pebble_index.catalog import ActionSpec, Catalog, builtin_catalog, spec_from_toml
from pebble_index.classify import Action, classify, classify_rules
from pebble_index.config import Config
from pebble_index.dispatch import attach_screenshot, prompt_with_screenshot, run_command
from pebble_index.ingest import classify_payload
from pebble_index.screen import capture_active_window


LOOK_PATTERN = (
    r"(?i)\b(?:what(?:['’]?s| is) (?:this|that|on (?:my |the )?screen)"
    r"|how does (?:this|that) work|explain (?:this|that)|what am i looking at)\b"
)


class LookClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Europe/London"))
        specs = {spec.id: spec for spec in builtin_catalog().all()}
        specs["look"] = ActionSpec(
            id="look",
            label="Look",
            description="Explain the focused window.",
            match="regex",
            pattern=LOOK_PATTERN,
            builtin="agent",
            context=["active-window"],
            require=["agent_enabled"],
            priority=90,
            fields=["title", "prompt"],
        )
        self.enabled = Config(agent_enabled=True, catalog=Catalog(specs))
        self.disabled = Config(agent_enabled=False, catalog=Catalog(specs))

    def test_what_is_this_is_look(self) -> None:
        action = classify_rules("what is this", self.recorded, self.enabled)
        self.assertEqual(action.name, "look")

    def test_how_does_this_work_is_look(self) -> None:
        action = classify_rules("how does this work", self.recorded, self.enabled)
        self.assertEqual(action.name, "look")

    def test_whats_on_my_screen_is_look(self) -> None:
        action = classify_rules("what's on my screen", self.recorded, self.enabled)
        self.assertEqual(action.name, "look")

    def test_plain_request_stays_agent(self) -> None:
        action = classify_rules("summarise my inbox", self.recorded, self.enabled)
        self.assertEqual(action.name, "agent")

    def test_look_beats_cloud_classifier(self) -> None:
        cloud = Config(classifier="cloud", agent_enabled=True, catalog=self.enabled.actions())
        action = classify("what is this", self.recorded, cloud)
        self.assertEqual(action.name, "look")

    def test_look_disabled_without_agent(self) -> None:
        action = classify_rules("what is this", self.recorded, self.disabled)
        self.assertEqual(action.name, "note")


class ScreenshotTests(unittest.TestCase):
    def test_command_expands_screenshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.sh"
            script.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
            script.chmod(0o755)
            spec = ActionSpec(
                id="echo",
                label="Echo",
                description="",
                command=["{dir}/run.sh", "{screenshot}"],
                source=Path(tmp) / "echo.toml",
            )
            result = run_command(
                spec,
                Action("echo", title="hi", extra={"screenshot": "/tmp/window.png"}),
                "abc",
            )
            self.assertEqual(result, "/tmp/window.png")

    def test_prompt_includes_screenshot_path(self) -> None:
        prompt = prompt_with_screenshot(
            Action("look", title="what is this", prompt="what is this", extra={"screenshot": "/tmp/win.png"})
        )
        self.assertIn("/tmp/win.png", prompt)
        self.assertIn("what is this", prompt)

    def test_attach_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window.png"
            path.write_bytes(b"png")
            spec = ActionSpec(id="look", label="Look", description="", context=["active-window"])
            action = Action("look", title="what is this", extra={"screenshot": str(path)})
            self.assertEqual(attach_screenshot(spec, action, "abc123"), str(path))

    def test_capture_active_window_uses_hypr_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "shot.png"

            def run(command, **kwargs):
                if command[:2] == ["hyprctl", "activewindow"]:
                    payload = json.dumps({"at": [12, 40], "size": [800, 600]})
                    return _result(0, payload)
                if command[0] == "grim":
                    dest.write_bytes(b"png")
                    self.assertEqual(command[2], "12,40 800x600")
                    return _result(0, "")
                return _result(1, "")

            captured = capture_active_window(dest, run=run)
            self.assertEqual(captured, dest)

    def test_voice_plugin_look_toml(self) -> None:
        path = Path(__file__).resolve().parents[2] / "omarchy-pebble-voice" / "pebble-index" / "look.toml"
        if not path.is_file():
            self.skipTest("voice plugin checkout is not a sibling of this repo")
        spec = spec_from_toml(path)
        self.assertEqual(spec.id, "look")
        self.assertEqual(spec.builtin, "agent")
        self.assertEqual(spec.context, ["active-window"])
        recorded = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Europe/London"))
        catalog = Catalog({**{item.id: item for item in builtin_catalog().all()}, spec.id: spec})
        action = classify_rules("what is this", recorded, Config(agent_enabled=True, catalog=catalog))
        self.assertEqual(action.name, "look")

    def test_toml_context_parses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "look.toml"
            path.write_text(
                "\n".join(
                    [
                        'id = "look"',
                        'label = "Look"',
                        'match = "regex"',
                        f'pattern = "{LOOK_PATTERN}"',
                        'builtin = "agent"',
                        'context = ["active-window"]',
                    ]
                ),
                encoding="utf-8",
            )
            spec = spec_from_toml(path)
            self.assertEqual(spec.context, ["active-window"])
            self.assertEqual(spec.builtin, "agent")


class ClassifyPayloadTests(unittest.TestCase):
    def test_payload_includes_screenshot(self) -> None:
        config = Config(agent_enabled=True, catalog=builtin_catalog())
        recorded = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Europe/London"))
        with patch("pebble_index.ingest.classify") as classify:
            classify.return_value = Action("agent", title="hello", prompt="hello")
            payload = classify_payload("hello", config=config, recorded_at=recorded, screenshot="/tmp/a.png")
        self.assertEqual(payload["action"], "agent")
        self.assertEqual(payload["screenshot"], "/tmp/a.png")
        self.assertEqual(payload["label"], "Agent")


def _result(code: int, stdout: str):
    class Result:
        returncode = code
        stdout = ""
        stderr = ""

    result = Result()
    result.returncode = code
    result.stdout = stdout
    result.stderr = ""
    return result


if __name__ == "__main__":
    unittest.main()
