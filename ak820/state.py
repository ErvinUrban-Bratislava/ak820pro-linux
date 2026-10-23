"""Payload-dedup persistence (v0.8.0, REVIEW.md §10.4 item 3).

`check_and_record_payload_hash` / `save_payload_hash` lived in
milestone_radar.py, which forced the *protocol* layer (`upload_frames_to_screen`)
to `import milestone_radar` — the §2.1 layering inversion. They now live here
with a stdlib-only dependency and an `AK820_STATE_PATH` env override that makes
dedup fully testable without touching ~/.cache.

§4.5: the digest is computed over a `memoryview` of the payload, so hashing a
worst-case ~8 MB upload never forces an extra `bytes(payload)` copy.
"""

import datetime
import hashlib
import json
import os

DEFAULT_STATE_PATH = os.path.expanduser("~/.cache/ak820_screen_state.json")


def get_state_path() -> str:
    """State file location; $AK820_STATE_PATH overrides the default cache path."""
    override = os.environ.get("AK820_STATE_PATH")
    if override:
        return os.path.expanduser(override)
    return DEFAULT_STATE_PATH


def check_and_record_payload_hash(payload, mode_desc: str, force: bool = False):
    """Return (should_upload, sha256_hex). Skips upload if identical payload is already on screen."""
    digest = hashlib.sha256(memoryview(payload)).hexdigest()
    path = get_state_path()
    if not force and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("sha256") == digest:
                return False, digest
        except Exception:
            pass
    return True, digest


def save_payload_hash(digest: str, mode_desc: str):
    path = get_state_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "sha256": digest,
                "mode": mode_desc,
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )
