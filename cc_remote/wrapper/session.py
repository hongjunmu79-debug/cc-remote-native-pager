"""Persist the cc session id so a wrapper restart can --resume it.

Keyed by cwd: different projects have different sessions, and resume requires
the cwd to match (the session jsonl lives under
~/.claude/projects/<cwd-with-/-as->/).
"""
from __future__ import annotations

import json
import os
import hashlib
import re
from pathlib import Path

from cc_remote.file_lock import fsync_directory, set_path_mode
from cc_remote.log import logger

log = logger("cc_remote.wrapper.session")

_STATE_FILE_MAX_BYTES = 16 * 1024
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


def _utf8_prefix(value: str, max_bytes: int) -> str:
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _session_file(state_dir: Path, cc_cwd: str) -> Path:
    # Preserve the established POSIX filename mapping. Windows paths need a
    # separate branch: backslashes and a drive colon otherwise escape the
    # sessions directory when interpreted by pathlib on Windows.
    if "\\" in cc_cwd or re.match(r"^[A-Za-z]:", cc_cwd):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cc_cwd).strip("_.") or "root"
        safe = (
            _utf8_prefix(safe, 140)
            + "-"
            + hashlib.sha256(cc_cwd.encode()).hexdigest()[:24]
        )
    else:
        safe = cc_cwd.replace("/", "_").strip("_") or "root"
    if len(safe.encode("utf-8")) > 200:
        safe = (_utf8_prefix(safe, 80) + "-"
                + hashlib.sha256(cc_cwd.encode()).hexdigest()[:24])
    return state_dir / "sessions" / f"{safe}.json"


def load_session_id(state_dir: Path, cc_cwd: str) -> str | None:
    f = _session_file(state_dir, cc_cwd)
    if not f.exists():
        return None
    try:
        if f.stat().st_size > _STATE_FILE_MAX_BYTES:
            raise ValueError("session state file exceeds size limit")
        with f.open() as stream:
            data = json.loads(stream.read(_STATE_FILE_MAX_BYTES + 1))
        sid = data.get("cc_session_id")
        if not isinstance(sid, str) or not _SAFE_SESSION_ID.fullmatch(sid):
            raise ValueError("session state contains an invalid id")
        log.debug("loaded session id", path=str(f), has_id=bool(sid))
        return sid
    except Exception as e:
        log.warning("failed to read session file", path=str(f), error=str(e))
        return None


def save_session_id(state_dir: Path, cc_cwd: str, session_id: str) -> None:
    f = _session_file(state_dir, cc_cwd)
    f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    set_path_mode(f.parent, 0o700)
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    payload = json.dumps({"cc_session_id": session_id, "cc_cwd": cc_cwd})
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    set_path_mode(tmp, 0o600)
    os.replace(tmp, f)
    fsync_directory(f.parent)
    log.debug("saved session id", path=str(f), session_id=session_id)
