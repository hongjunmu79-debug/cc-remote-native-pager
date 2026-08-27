"""Static scans that must pass before any distributable asset is built.

Two scans live here so both local ``pytest`` runs and CI jobs share one
implementation:

- :func:`scan_forbidden_literals` fails on the old developer-machine identity
  (the ``23715`` username and the pre-release ``192.168.3.4`` LAN IP) and on
  absolute Windows node paths, which must never appear in a public repository.
  The module's own literal definitions are exempted by skipping this file.
- :func:`scan_high_confidence_secrets` fails on secret *indicators* with very
  low false-positive rates. Real credentials are never acceptable in tracked
  source; a line may opt out with a ``cc-remote-scan-allow`` marker comment,
  which is how test fixtures keep deliberately fake tokens.

Both scans operate on tracked text files only (git is authoritative about what
could be shipped). The ``--allow-known-fixtures`` flag is used by tests that
deliberately exercise the scanner itself.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

FORBIDDEN_LITERALS: dict[str, str] = {
    "23715": "developer-machine username",  # cc-remote-scan-allow: definition of the literal being scanned
    "192.168.3.4": "pre-release machine-specific LAN IP",  # cc-remote-scan-allow: definition of the literal being scanned
    "Program Files\\nodejs": "hardcoded Node LAN-proxy install path",  # cc-remote-scan-allow: definition of the literal being scanned
}

_ALLOW_MARKER = "cc-remote-scan-allow"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_AWS_ACCESS_KEY_ID_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b")
_GITHUB_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_STRIPE_KEY_RE = re.compile(r"\b(?:sk_live|rk_live)_[A-Za-z0-9]{24,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_ANTHROPIC_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key block", _PRIVATE_KEY_RE),
    ("AWS access key id", _AWS_ACCESS_KEY_ID_RE),
    ("GitHub classic token", _GITHUB_TOKEN_RE),
    ("GitHub fine-grained PAT", _GITHUB_PAT_RE),
    ("Slack token", _SLACK_TOKEN_RE),
    ("Stripe live key", _STRIPE_KEY_RE),
    ("OpenAI-style API key", _OPENAI_KEY_RE),
    ("Anthropic-style API key", _ANTHROPIC_KEY_RE),
    ("Google API key", _GOOGLE_API_KEY_RE),
)

_TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".env", ".sh", ".bat", ".ps1", ".psm1",
    ".psd1", ".kt", ".kts", ".xml", ".html", ".css", ".properties",
    ".service", ".plist", ".lock", ".example", ".dist", ".in", ".gitignore",
    ".gitattributes", "LICENSE", "AGENTS", "CLAUDE", "CHANGELOG", "README",
}
_IGNORE_FILENAMES = {
    ".gitignore",
    ".gitattributes",
}
_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "test-results",
    ".gradle",
    ".idea",
    ".kotlin",
}


def _is_tracked_text_file(path: Path, root: Path) -> bool:
    if path.name in _IGNORE_FILENAMES:
        return True
    suffix = path.suffix.lower()
    if suffix and suffix in _TEXT_SUFFIXES:
        return True
    if suffix == "" and path.name in {
        "LICENSE", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md", "README.md",
    }:
        return True
    return False


def _iter_tracked(root: Path) -> Iterable[Path]:
    # Prefer git when available so build output never enters the scan. The
    # fallback walk skips known generated/third-party directories.
    try:
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(root),
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts):
                continue
            yield path
        return
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        yield root / raw.decode("utf-8", errors="replace")


def scan_forbidden_literals(root: Path) -> list[str]:
    """Return human-readable violations for machine-specific identities."""
    violations: list[str] = []
    # This module defines the literals it scans for; never self-report its own
    # definition lines.
    scanner_module = Path(__file__).resolve()
    for path in _iter_tracked(root):
        if not _is_tracked_text_file(path, root):
            continue
        if path.resolve() == scanner_module:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            if _ALLOW_MARKER in line:
                continue
            for literal, description in FORBIDDEN_LITERALS.items():
                if literal in line:
                    violations.append(
                        f"{path.relative_to(root)}:{line_number}: "
                        f"{description} {literal!r}"
                    )
    return violations


def scan_high_confidence_secrets(root: Path) -> list[str]:
    """Return human-readable violations for real-secret indicators."""
    violations: list[str] = []
    # This module defines the indicator patterns it scans for; never report its
    # own pattern definitions as live credentials.
    scanner_module = Path(__file__).resolve()
    for path in _iter_tracked(root):
        if not _is_tracked_text_file(path, root):
            continue
        if path.resolve() == scanner_module:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            if _ALLOW_MARKER in line:
                continue
            for label, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        f"{path.relative_to(root)}:{line_number}: {label} indicator"
                    )
                    break
    return violations
