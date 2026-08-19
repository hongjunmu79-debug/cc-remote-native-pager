#!/usr/bin/env python3
"""Validate a role-scoped cc-remote release directory without importing it."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import subprocess
from typing import Any

from deploy.validate_protocol_bundle import (
    ProtocolBundleError,
    backend_product_version,
    backend_protocol,
    validate_protocol_bundle,
)


class ReleaseManifestError(ValueError):
    pass


_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ROLES = {"relay", "wrapper"}
_SYSTEMS = {"linux", "darwin"}
_ARCHES = {"x86_64", "arm64"}
_KEYS = {
    "schema",
    "product_version",
    "protocol_version",
    "git_sha",
    "role",
    "os",
    "arch",
    "python",
    "uv",
}


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseManifestError(f"cannot read release manifest: {path}") from exc
    if not isinstance(value, dict) or set(value) != _KEYS:
        raise ReleaseManifestError("release manifest has an invalid schema")
    if value["schema"] != 1:
        raise ReleaseManifestError("unsupported release manifest schema")
    if value["role"] not in _ROLES:
        raise ReleaseManifestError("release manifest has an invalid role")
    if value["os"] not in _SYSTEMS:
        raise ReleaseManifestError("release manifest has an invalid operating system")
    if value["arch"] not in _ARCHES:
        raise ReleaseManifestError("release manifest has an invalid architecture")
    if value["role"] == "relay" and value["os"] != "linux":
        raise ReleaseManifestError("relay bundles only support linux")
    if not isinstance(value["git_sha"], str) or not _SHA_RE.fullmatch(
        value["git_sha"]
    ):
        raise ReleaseManifestError("release manifest has an invalid git SHA")
    if not isinstance(value["python"], str) or not re.fullmatch(
        r"3\.13\.[0-9]+", value["python"]
    ):
        raise ReleaseManifestError("release manifest has an unsupported Python runtime")
    if not isinstance(value["uv"], str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", value["uv"]
    ):
        raise ReleaseManifestError("release manifest has an invalid uv version")
    return value


def validate_release_directory(
    root: Path,
    *,
    expected_role: str | None = None,
    expected_system: str | None = None,
    expected_arch: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = load_manifest(root / "release-manifest.json")
    expected = {
        "role": expected_role,
        "os": expected_system,
        "arch": expected_arch,
    }
    for key, value in expected.items():
        if value is not None and manifest[key] != value:
            raise ReleaseManifestError(
                f"release {key} mismatch: expected {value}, got {manifest[key]}"
            )

    init_path = root / "cc_remote" / "__init__.py"
    protocol_path = root / "cc_remote" / "protocol.py"
    try:
        product_version = backend_product_version(init_path)
        protocol_version = backend_protocol(protocol_path)
    except ProtocolBundleError as exc:
        raise ReleaseManifestError(str(exc)) from exc
    if manifest["product_version"] != product_version:
        raise ReleaseManifestError("release product version does not match backend")
    if manifest["protocol_version"] != protocol_version:
        raise ReleaseManifestError("release protocol version does not match backend")

    role = manifest["role"]
    lock_path = root / f"requirements-{role}.lock"
    if not lock_path.is_file():
        raise ReleaseManifestError(f"{lock_path.name} is missing")
    uv_path = root / "bin" / "uv"
    if not uv_path.is_file() or not uv_path.stat().st_mode & 0o111:
        raise ReleaseManifestError("bundled uv executable is missing")
    try:
        uv_output = subprocess.run(
            [uv_path, "--version"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseManifestError("bundled uv executable could not run") from exc
    uv_match = re.fullmatch(
        r"uv ([0-9]+\.[0-9]+\.[0-9]+)(?: \([^\r\n]+\))?",
        uv_output,
    )
    actual_uv_version = uv_match.group(1) if uv_match else ""
    if actual_uv_version != manifest["uv"]:
        raise ReleaseManifestError("bundled uv version does not match manifest")
    if not (root / "licenses" / "uv-LICENSE-MIT").is_file():
        raise ReleaseManifestError("bundled uv license is missing")

    if role == "relay":
        web_manifest = root / "web" / "dist" / "cc-remote-build.json"
        try:
            validate_protocol_bundle(protocol_path, web_manifest, init_path)
        except ProtocolBundleError as exc:
            raise ReleaseManifestError(str(exc)) from exc
        if not (root / "web" / "dist" / "index.html").is_file():
            raise ReleaseManifestError("relay web build is missing")
    elif (root / "web").exists():
        raise ReleaseManifestError("wrapper bundle must not contain web assets")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--role", choices=sorted(_ROLES))
    parser.add_argument("--os", dest="system", choices=sorted(_SYSTEMS))
    parser.add_argument("--arch", choices=sorted(_ARCHES))
    args = parser.parse_args()
    try:
        manifest = validate_release_directory(
            args.root,
            expected_role=args.role,
            expected_system=args.system,
            expected_arch=args.arch,
        )
    except ReleaseManifestError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"cc-remote v{manifest['product_version']} "
        f"protocol v{manifest['protocol_version']} "
        f"{manifest['role']} {manifest['os']}/{manifest['arch']} "
        f"Python {manifest['python']} uv {manifest['uv']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
