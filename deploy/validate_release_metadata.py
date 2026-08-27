#!/usr/bin/env python3
"""Validate every release tier against the canonical release metadata source.

Reads ``deploy/release-metadata.json`` and confirms the backend, web build
manifest, install bootstrap, Android package defaults, documentation URLs, and
machine-specific-identity scans all agree with it. This is the same gate CI
runs; it never imports the runtime package tree.
"""
# ruff: noqa: E402  # the sys.path bootstrap below must run before `deploy` imports
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the repo root importable so this module works both as a script
# (``python deploy/validate_release_metadata.py`` — used by build.ps1 and CI)
# and as a package member (``python -m deploy.validate_release_metadata`` or
# pytest imports). Script invocation sets sys.path[0] to deploy/, which does
# not contain the ``deploy`` package itself.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from deploy.release_metadata import (
    ReleaseMetadataError,
    load_release_metadata,
    repository_root,
)
from deploy.release_scan import (
    scan_forbidden_literals,
    scan_high_confidence_secrets,
)
from deploy.validate_protocol_bundle import (
    ProtocolBundleError,
    backend_product_version,
    backend_protocol,
)


class ReleaseValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseValidationError(message)


def validate_release_metadata(root: Path) -> None:
    metadata_path = root / "deploy" / "release-metadata.json"
    metadata = load_release_metadata(metadata_path)

    # Backend product + wire protocol.
    init_path = root / "cc_remote" / "__init__.py"
    protocol_path = root / "cc_remote" / "protocol.py"
    try:
        backend_version = backend_product_version(init_path)
        backend_protocol_version = backend_protocol(protocol_path)
    except ProtocolBundleError as exc:
        raise ReleaseValidationError(str(exc)) from exc
    _require(
        backend_version == metadata.product_version,
        f"cc_remote/__init__.py version {backend_version!r} != "
        f"product_version {metadata.product_version!r}",
    )
    _require(
        backend_protocol_version == metadata.protocol,
        f"cc_remote/protocol.py PROTOCOL_VERSION {backend_protocol_version} != "
        f"protocol {metadata.protocol}",
    )

    # Web client.
    package = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (root / "web/package-lock.json").read_text(encoding="utf-8")
    )
    build_manifest = json.loads(
        (root / "web/public/cc-remote-build.json").read_text(encoding="utf-8")
    )
    _require(
        package["version"] == metadata.product_version,
        "web/package.json version does not match product_version",
    )
    _require(
        package_lock["version"] == metadata.product_version
        and package_lock["packages"][""]["version"] == metadata.product_version,
        "web/package-lock.json version does not match product_version",
    )
    _require(
        build_manifest.get("version") == metadata.product_version
        and build_manifest.get("protocol") == metadata.protocol,
        "web/public/cc-remote-build.json does not match release metadata",
    )

    # install.sh bootstrap defaults.
    installer = (root / "deploy/install.sh").read_text(encoding="utf-8")
    _require(
        f'VERSION="${{CC_REMOTE_VERSION:-{metadata.distribution_version}}}"'
        in installer,
        "deploy/install.sh VERSION default does not match distribution_version",
    )
    _require(
        f'REPOSITORY="${{CC_REMOTE_GITHUB_REPOSITORY:-{metadata.repository.slug}}}"'
        in installer,
        "deploy/install.sh REPOSITORY default does not match repository identity",
    )

    # Android package defaults come from the metadata source, never literals.
    gradle = (root / "android-native/app/build.gradle.kts").read_text(
        encoding="utf-8"
    )
    _require(
        "release-metadata.json" in gradle,
        "android build.gradle.kts must consume release-metadata.json",
    )
    _require(
        metadata.android.version_name == metadata.distribution_version,
        "android version_name must equal the distribution version",
    )
    for literal in ("30010", "3.0.0-pager.1", "http://192.168.3.4"):  # cc-remote-scan-allow: stale literals being guarded against
        _require(
            literal not in gradle,
            f"android build.gradle.kts still contains stale literal {literal!r}",
        )

    # READMEs reference the real repository and distribution release.
    for readme_name in ("README.md", "README_en.md"):
        readme = (root / readme_name).read_text(encoding="utf-8")
        _require(
            f"github.com/{metadata.repository.slug}" in readme,
            f"{readme_name} must reference {metadata.repository.slug}",
        )
        _require(
            f"v{metadata.distribution_version}" in readme,
            f"{readme_name} must reference v{metadata.distribution_version}",
        )

    # Machine-specific identity scans.
    forbidden = scan_forbidden_literals(root)
    _require(not forbidden, "forbidden machine-specific literals found:\n" + "\n".join(forbidden))
    secrets = scan_high_confidence_secrets(root)
    _require(not secrets, "high-confidence secret indicators found:\n" + "\n".join(secrets))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    root = repository_root(args.root)
    try:
        validate_release_metadata(root)
    except (ReleaseMetadataError, ReleaseValidationError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    metadata = load_release_metadata(root / "deploy" / "release-metadata.json")
    print(
        f"cc-remote {metadata.product_version} "
        f"distribution {metadata.distribution_version} "
        f"protocol v{metadata.protocol} "
        f"android {metadata.android.application_id} "
        f"vc{metadata.android.version_code} "
        f"repository {metadata.repository.slug}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
