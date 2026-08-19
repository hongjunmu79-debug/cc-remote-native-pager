#!/usr/bin/env python3
"""Atomically replace a release symlink on Linux or macOS."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: atomic_symlink.py TARGET LINK", file=sys.stderr)
        return 2
    target = Path(sys.argv[1]).resolve()
    link = Path(sys.argv[2])
    if not target.is_dir():
        print(f"ERROR: release target is not a directory: {target}", file=sys.stderr)
        return 1
    if link.exists() and not link.is_symlink():
        print(f"ERROR: current path is not a symlink: {link}", file=sys.stderr)
        return 1
    link.parent.mkdir(parents=True, exist_ok=True)
    staged = link.with_name(f".{link.name}.next-{secrets.token_hex(8)}")
    try:
        os.symlink(target, staged)
        os.replace(staged, link)
    finally:
        staged.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
