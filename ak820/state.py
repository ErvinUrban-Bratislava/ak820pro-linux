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
import tempfile

DEFAULT_STATE_PATH = os.path.expanduser("~/.cache/ak820_screen_state.json")


def get_state_path() -> str:
    """State file location; $AK820_STATE_PATH overrides the default cache path."""
    override = os.environ.get("AK820_STATE_PATH")
    if override:
        return os.path.expanduser(override)
    return DEFAULT_STATE_PATH


def _read_state() -> dict:
    """Current state doc, or {} when absent/corrupt — every reader fails dirty."""
    path = get_state_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict):
                return doc
        except Exception:
            pass
    return {}


def _write_state(doc: dict) -> None:
    """Atomic read-modify-write target (m6 idiom: mkstemp + os.replace).

    A crash mid-write can never leave a truncated state file, and a bare
    relative filename (dirname == "") resolves to "." instead of raising.
    """
    path = get_state_path()
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".state-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def check_and_record_payload_hash(payload, mode_desc: str, force: bool = False):
    """Return (should_upload, sha256_hex). Skips upload if identical payload is already on screen."""
    digest = hashlib.sha256(memoryview(payload)).hexdigest()
    if not force and _read_state().get("sha256") == digest:
        return False, digest
    return True, digest


def save_payload_hash(digest: str, mode_desc: str):
    """Record the on-screen payload — read-modify-write so the §3.5 pipe_clean
    flag set by mark_pipe() survives the rewrite."""
    doc = _read_state()
    doc.update(
        {
            "sha256": digest,
            "mode": mode_desc,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    )
    _write_state(doc)


# --- §3.5/§4.1: alignment-skip on known-clean pipe (v0.13.0) ------------------
# After a fully-ACKed stream + SAVE the firmware sub-packet counter
# [0x2000003A] is provably 0 (FINDINGS.md §4), so the next upload can skip the
# ~1.6-2 s align_data_pipe entirely. The flag is flipped to dirty *before*
# streaming, so any timeout/crash in between re-aligns on the next run.


def pipe_known_clean() -> bool:
    """True only when the previous upload ended fully-ACKed + SAVE'd."""
    return _read_state().get("pipe_clean") is True


def mark_pipe(clean: bool) -> None:
    """Persist the pipe-clean flag without disturbing the dedup fields."""
    doc = _read_state()
    doc["pipe_clean"] = bool(clean)
    doc["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _write_state(doc)
