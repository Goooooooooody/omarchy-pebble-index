from __future__ import annotations

import hashlib


def event_id(client: str, recorded_at: str, transcription: str) -> str:
    payload = f"{client}|{recorded_at}|{transcription}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
