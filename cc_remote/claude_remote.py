"""Explicit ``python -m cc_remote.claude_remote`` entry point.

This intentionally has a distinct name and never replaces the official
``claude`` executable.
"""

from cc_remote.claude_broker.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
