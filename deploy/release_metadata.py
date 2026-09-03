"""Canonical release metadata source loader.

``deploy/release-metadata.json`` is the single machine-readable source of truth
for the base product version, the pager distribution version, the wire
protocol, the public repository identity, and the Android package defaults.
Build and verification scripts consume or validate this file so unchecked
literals cannot drift between tiers.

The product version (``3.0.0``) identifies the shared backend/web codebase.
The distribution version (``3.0.0-pager.12``) identifies the Android-pager
release line and is what release tags (``v3.0.0-pager.12``) and install scripts
target. Protocol 19 is unchanged by the pager distribution.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RELEASE_METADATA_FILENAME = "release-metadata.json"


class ReleaseMetadataError(ValueError):
    pass


_PRODUCT_VERSION_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_DISTRIBUTION_VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_APPLICATION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseMetadataError(message)


@dataclass(frozen=True)
class AndroidMetadata:
    application_id: str
    version_name: str
    version_code: int
    signer_sha256: str


@dataclass(frozen=True)
class RepositoryIdentity:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class ReleaseMetadata:
    schema: int
    product_version: str
    distribution_version: str
    protocol: int
    repository: RepositoryIdentity
    android: AndroidMetadata

    @property
    def release_tag(self) -> str:
        """Release tag (`v3.0.0-pager.12`) used by GitHub releases and installers."""
        return f"v{self.distribution_version}"


def load_release_metadata(path: Path | str) -> ReleaseMetadata:
    """Load and structurally validate the canonical release metadata file."""
    metadata_path = Path(path)
    try:
        raw: Any = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReleaseMetadataError(
            f"cannot read release metadata: {metadata_path}"
        ) from exc
    if not isinstance(raw, dict):
        raise ReleaseMetadataError("release metadata must be a JSON object")

    _require(raw.get("schema") == 1, "release metadata schema must be 1")
    schema = 1

    product_version = raw.get("product_version")
    _require(
        isinstance(product_version, str)
        and _PRODUCT_VERSION_RE.fullmatch(product_version) is not None,
        "product_version must be an exact semantic version",
    )

    distribution_version = raw.get("distribution_version")
    _require(
        isinstance(distribution_version, str)
        and _DISTRIBUTION_VERSION_RE.fullmatch(distribution_version) is not None,
        "distribution_version must be a semantic version with optional pre-release",
    )
    _require(
        distribution_version.startswith(f"{product_version}-"),
        "distribution_version must extend the product version",
    )

    protocol = raw.get("protocol")
    _require(
        isinstance(protocol, int) and not isinstance(protocol, bool) and protocol > 0,
        "protocol must be a positive integer",
    )

    repository = raw.get("repository")
    _require(isinstance(repository, dict), "repository must be an object")
    owner = repository.get("owner") if isinstance(repository, dict) else None
    name = repository.get("name") if isinstance(repository, dict) else None
    _require(
        isinstance(owner, str) and _OWNER_RE.fullmatch(owner) is not None,
        "repository.owner is invalid",
    )
    _require(
        isinstance(name, str) and _NAME_RE.fullmatch(name) is not None,
        "repository.name is invalid",
    )

    android = raw.get("android")
    _require(isinstance(android, dict), "android must be an object")
    application_id = android.get("application_id") if isinstance(android, dict) else None
    version_name = android.get("version_name") if isinstance(android, dict) else None
    version_code = android.get("version_code") if isinstance(android, dict) else None
    signer_sha256 = android.get("signer_sha256") if isinstance(android, dict) else None
    _require(
        isinstance(application_id, str)
        and _APPLICATION_ID_RE.fullmatch(application_id) is not None,
        "android.application_id is invalid",
    )
    _require(
        isinstance(version_name, str)
        and _DISTRIBUTION_VERSION_RE.fullmatch(version_name) is not None,
        "android.version_name is invalid",
    )
    _require(
        isinstance(version_code, int)
        and not isinstance(version_code, bool)
        and version_code > 0,
        "android.version_code must be a positive integer",
    )
    # The APK signer's certificate SHA-256 digest, in the same form apksigner
    # prints it. This is the exact fingerprint release.yml verifies before the
    # APK can be uploaded or published; a wrong fingerprint must never be
    # substituted for the real key.
    _require(
        isinstance(signer_sha256, str) and _SHA256_HEX_RE.fullmatch(signer_sha256) is not None,
        "android.signer_sha256 must be a 64-character lowercase hex SHA-256 digest",
    )

    return ReleaseMetadata(
        schema=schema,
        product_version=product_version,
        distribution_version=distribution_version,
        protocol=protocol,
        repository=RepositoryIdentity(owner=owner, name=name),
        android=AndroidMetadata(
            application_id=application_id,
            version_name=version_name,
            version_code=version_code,
            signer_sha256=signer_sha256,
        ),
    )


def repository_root(start: Path) -> Path:
    """Return the repository root containing ``deploy/release-metadata.json``."""
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    while True:
        if (candidate / "deploy" / RELEASE_METADATA_FILENAME).is_file():
            return candidate
        if candidate.parent == candidate:
            raise ReleaseMetadataError("repository root not found")
        candidate = candidate.parent


def load_repository_metadata(start: Path) -> ReleaseMetadata:
    root = repository_root(start)
    return load_release_metadata(root / "deploy" / RELEASE_METADATA_FILENAME)
