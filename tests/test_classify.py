from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from pebble_index.catalog import ActionSpec, Catalog, builtin_catalog, spec_from_toml
from pebble_index.classify import action_from_model, classify_rules, parse_model_json
from pebble_index.config import Config, load_dotenv, resolve_secret
from pebble_index.dispatch import run_command
from pebble_index.ids import event_id


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded = datetime(2026, 9, 2, 11, 0, tzinfo=ZoneInfo("Europe/London"))
        self.config = Config(agent_enabled=False, catalog=builtin_catalog())

    def test_event_id_is_stable(self) -> None:
        first = event_id("ring", "1700000000000", "hello")
        second = event_id("ring", "1700000000000", "hello")
        self.assertEqual(first, second)
        self.assertNotEqual(first, event_id("ring", "1700000000000", "hello!"))

    def test_default_is_note(self) -> None:
        action = classify_rules("buy oat milk", self.recorded, self.config)
        self.assertEqual(action.name, "note")
        self.assertEqual(action.title, "buy oat milk")

    def test_relative_reminder(self) -> None:
        action = classify_rules("remind me in 20 minutes to check the oven", self.recorded, self.config)
        self.assertEqual(action.name, "reminder")
        self.assertEqual(action.minutes, 20)
        self.assertIn("oven", action.title)

    def test_long_relative_becomes_calendar(self) -> None:
        action = classify_rules("remind me in 36 hours to file taxes", self.recorded, self.config)
        self.assertEqual(action.name, "calendar")
        self.assertIsNotNone(action.when)

    def test_wake_phrase_disabled_is_note(self) -> None:
        action = classify_rules("herdr summarise my inbox", self.recorded, self.config)
        self.assertEqual(action.name, "note")

    def test_wake_phrase_must_be_at_start(self) -> None:
        enabled = Config(agent_enabled=True, catalog=builtin_catalog())
        buried = classify_rules("tell the estate agent I will be late", self.recorded, enabled)
        self.assertEqual(buried.name, "note")
        woken = classify_rules("herdr summarise my inbox", self.recorded, enabled)
        self.assertEqual(woken.name, "herdr")
        self.assertEqual(woken.prompt, "summarise my inbox")

    def test_dated_phrase_becomes_calendar(self) -> None:
        action = classify_rules("meeting tomorrow 3pm with Sam", self.recorded, self.config)
        self.assertEqual(action.name, "calendar")
        self.assertIsNotNone(action.when)
        self.assertEqual(action.when.hour, 15)

    def test_agent_word_alone_is_not_enough(self) -> None:
        enabled = Config(agent_enabled=True, catalog=builtin_catalog())
        action = classify_rules("agent", self.recorded, enabled)
        self.assertEqual(action.name, "note")

    def test_parse_model_json_accepts_fences(self) -> None:
        data = parse_model_json('```json\n{"action":"note","title":"milk"}\n```')
        self.assertEqual(data["action"], "note")

    def test_resolve_secret_from_env_file(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        previous = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text("OPENROUTER_API_KEY=sk-test-from-env\n", encoding="utf-8")
                load_dotenv(path)
                self.assertEqual(resolve_secret("", "OPENROUTER_API_KEY"), "sk-test-from-env")
                self.assertEqual(resolve_secret("$OPENROUTER_API_KEY"), "sk-test-from-env")
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
            if previous is not None:
                os.environ["OPENROUTER_API_KEY"] = previous

    def _with_shop(self, *, enabled: bool = True) -> Config:
        specs = {spec.id: spec for spec in builtin_catalog().all()}
        specs["shop"] = ActionSpec(
            id="shop",
            label="Shopping",
            description="Add an item to the shopping list.",
            enabled=enabled,
            priority=80,
            match="wake",
            wake=["shop", "shopping"],
            fields=["title"],
        )
        return Config(agent_enabled=False, catalog=Catalog(specs))

    def test_community_wake_action(self) -> None:
        action = classify_rules("shop oat milk", self.recorded, self._with_shop())
        self.assertEqual(action.name, "shop")
        self.assertEqual(action.title, "oat milk")

    def test_disabled_community_action_is_note(self) -> None:
        action = classify_rules("shop oat milk", self.recorded, self._with_shop(enabled=False))
        self.assertEqual(action.name, "note")

    def test_model_accepts_registered_custom_action(self) -> None:
        action = action_from_model(
            {"action": "shop", "title": "oat milk", "body": "shop oat milk"},
            "shop oat milk",
            self.recorded,
            self._with_shop(),
        )
        self.assertEqual(action.name, "shop")

    def test_model_unknown_action_is_note(self) -> None:
        action = action_from_model(
            {"action": "teleport", "title": "nope"},
            "teleport home",
            self.recorded,
            self.config,
        )
        self.assertEqual(action.name, "note")

    def test_regex_community_action(self) -> None:
        specs = {spec.id: spec for spec in builtin_catalog().all()}
        specs["lights"] = ActionSpec(
            id="lights",
            label="Lights",
            description="",
            match="regex",
            pattern=r"(?i)\bturn off the lights\b",
        )
        config = Config(catalog=Catalog(specs))
        action = classify_rules("please turn off the lights", self.recorded, config)
        self.assertEqual(action.name, "lights")

    def test_command_expands_placeholders(self) -> None:
        import tempfile
        from pathlib import Path

        from pebble_index.classify import Action

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.sh"
            script.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\"\n", encoding="utf-8")
            script.chmod(0o755)
            spec = ActionSpec(
                id="echo",
                label="Echo",
                description="",
                command=["{dir}/run.sh", "{title}"],
                source=Path(tmp) / "echo.toml",
            )
            result = run_command(spec, Action("echo", title="hello", body="hello"), "abc")
            self.assertEqual(result, "hello")

    def test_shipped_log_toml_parses(self) -> None:
        from pebble_index.paths import plugin_actions_dir

        spec = spec_from_toml(plugin_actions_dir() / "log.toml")
        self.assertEqual(spec.id, "log")
        self.assertFalse(spec.enabled)
        self.assertIn("log", spec.wake)


if __name__ == "__main__":
    unittest.main()
