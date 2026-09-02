from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pebble_index.bind import BindError, require_cgnat_ipv4
from pebble_index.catalog import ActionSpec, Catalog, builtin_catalog
from pebble_index.classify import Action
from pebble_index.config import Config
from pebble_index.dispatch import DispatchError, dispatch, run_command
from pebble_index.server import check_bearer, _parse_multipart


class BindTests(unittest.TestCase):
    def test_accepts_tailscale_cgnat(self) -> None:
        self.assertEqual(require_cgnat_ipv4("100.101.157.16"), "100.101.157.16")

    def test_rejects_wildcard_and_loopback(self) -> None:
        for address in ("0.0.0.0", "127.0.0.1", "192.168.1.10", "8.8.8.8"):
            with self.subTest(address=address):
                with self.assertRaises(BindError):
                    require_cgnat_ipv4(address)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(BindError):
            require_cgnat_ipv4("   ")


class BearerTests(unittest.TestCase):
    def test_matching_bearer(self) -> None:
        self.assertTrue(check_bearer("secret-token", "Bearer secret-token"))

    def test_wrong_value(self) -> None:
        self.assertFalse(check_bearer("secret-token", "Bearer other-token"))

    def test_wrong_length(self) -> None:
        self.assertFalse(check_bearer("secret-token", "Bearer short"))

    def test_missing_or_empty(self) -> None:
        self.assertFalse(check_bearer("secret-token", ""))
        self.assertFalse(check_bearer("secret-token", "secret-token"))
        self.assertFalse(check_bearer("", "Bearer secret-token"))


class MultipartTests(unittest.TestCase):
    def test_reads_transcription_and_skips_audio(self) -> None:
        boundary = "----pebble"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="transcription"\r\n\r\n'
            "buy oat milk\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="clip.m4a"\r\n'
            "Content-Type: audio/mp4\r\n\r\n"
            "not-audio-bytes\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="recordedAt"\r\n\r\n'
            "1700000000000\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        fields = _parse_multipart(f"multipart/form-data; boundary={boundary}", body)
        self.assertEqual(fields["transcription"], "buy oat milk")
        self.assertEqual(fields["recordedAt"], "1700000000000")
        self.assertNotIn("audio", fields)


class DispatchTests(unittest.TestCase):
    def test_calendar_without_when_writes_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(notes_inbox=tmp, catalog=builtin_catalog())
            result = dispatch(Action("calendar", title="standup", body="standup"), config, "abc12345ffff")
            self.assertTrue(result.startswith("calendar-failed-note:"))
            notes = list(Path(tmp).glob("*.md"))
            self.assertEqual(len(notes), 1)

    def test_command_failure_falls_back_to_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs = {spec.id: spec for spec in builtin_catalog().all()}
            specs["boom"] = ActionSpec(
                id="boom",
                label="Boom",
                description="",
                command=["false"],
                fallback="note",
                source=Path(tmp) / "boom.toml",
            )
            config = Config(notes_inbox=tmp, catalog=Catalog(specs))
            result = dispatch(Action("boom", title="nope", body="nope"), config, "def12345ffff")
            self.assertTrue(result.startswith("boom-failed-note:"))
            self.assertEqual(len(list(Path(tmp).glob("*.md"))), 1)

    def test_command_failure_without_fallback_raises(self) -> None:
        spec = ActionSpec(
            id="boom",
            label="Boom",
            description="",
            command=["false"],
            fallback="none",
        )
        with self.assertRaises(DispatchError):
            run_command(spec, Action("boom", title="nope", body="nope"), "id")


if __name__ == "__main__":
    unittest.main()
