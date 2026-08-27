"""Build and verify the Windows distribution manifest.

A distribution is the tree the installer extracts and runs from. Building the
manifest walks that tree, hashes every file, refuses ``.venv`` directories,
symlinks, and dev leftovers, and records the canonical version values so the
installer can verify the payload before touching the target machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

# When this module is invoked as a script (``python packaging/windows/
# win_manifest.py``) sys.path[0] is the ``packaging/windows`` directory, so the
# repository root is not importable. Insert it so ``packaging.windows``
# resolves whether we run from the source tree or from a release archive.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json
import shutil
from dataclasses import dataclass

from packaging.windows.win_layout import sha256_of, write_sha256sums

DISTRIBUTION_MANIFEST_NAME = "distribution-manifest.json"
_SHA256SUMS_NAME = "SHA256SUMS"
_FORBIDDEN_DIRS = {".venv", "__pycache__", ".git", "node_modules"}
_FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
_SKIP_NAMES = {".DS_Store", "Thumbs.db"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class DistributionInfo:
    distribution_version: str
    product_version: str
    protocol: int
    git_sha: str
    source_date_epoch: int


def iter_distribution_files(root: Path):
    """Yield non-forbidden regular files under [root] in sorted order.

    Forbidden directories (``.venv``, ``__pycache__``, ``.git``,
    ``node_modules``) are skipped here so a manifest can be built from a tree
    that still carries dev leftovers; ``verify_distribution`` is the gate that
    hard-rejects them on a built payload.
    """
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if not path.is_file():
            raise ManifestError(
                f"distribution contains an unsupported entry: {path}"
            )
        if path.name in _SKIP_NAMES or path.suffix in _FORBIDDEN_SUFFIXES:
            continue
        if any(part in _FORBIDDEN_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def build_manifest(root: Path, info: DistributionInfo) -> dict:
    """Build the distribution manifest dict (does not write it)."""
    files: dict[str, str] = {}
    # SHA256SUMS and the manifest itself are derived artifacts. Hashing them
    # back into the manifest would be circular and make a second manifest build
    # over the same tree (win_build.py rebuilds per archive, and build.ps1
    # assembles two archives from one payload) record stale self-checksums.
    # They are excluded here and still written + presence-checked separately,
    # so rebuilding over an already-built distribution is idempotent.
    derived = {_SHA256SUMS_NAME, DISTRIBUTION_MANIFEST_NAME}
    for path in iter_distribution_files(root):
        if path.name in derived:
            continue
        rel = path.relative_to(root).as_posix()
        files[rel] = sha256_of(path)
    return {
        "schema": 1,
        "product_version": info.product_version,
        "distribution_version": info.distribution_version,
        "protocol": info.protocol,
        "git_sha": info.git_sha,
        "source_date_epoch": info.source_date_epoch,
        "files": files,
    }


def write_manifest(root: Path, manifest: dict) -> Path:
    manifest_path = root / DISTRIBUTION_MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    files = [root / rel for rel in sorted(manifest["files"])]
    write_sha256sums(root, files, relroot=root)
    return manifest_path


def read_manifest(root: Path) -> dict:
    manifest_path = root / DISTRIBUTION_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read distribution manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ManifestError("distribution manifest has an unsupported schema")
    if not isinstance(payload.get("files"), dict):
        raise ManifestError("distribution manifest is missing the file map")
    return payload


def verify_distribution(root: Path, *, expected_version: str | None = None) -> list[str]:
    """Verify a staged distribution. Returns a list of problems (empty = OK)."""
    problems: list[str] = []
    try:
        manifest = read_manifest(root)
    except ManifestError as exc:
        return [str(exc)]

    if expected_version is not None and manifest.get("distribution_version") != expected_version:
        problems.append(
            f"distribution version mismatch: expected {expected_version}, "
            f"got {manifest.get('distribution_version')}"
        )

    for rel, expected in manifest["files"].items():
        path = root / rel
        if not path.is_file():
            problems.append(f"manifest file missing: {rel}")
            continue
        if sha256_of(path) != expected:
            problems.append(f"checksum mismatch: {rel}")

    # No stale symlinks or venv may appear even outside the manifest.
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            problems.append(f"distribution contains a symbolic link: {path}")
        elif path.is_dir() and path.name in _FORBIDDEN_DIRS:
            problems.append(f"distribution contains a {path.name} directory: {path}")

    sha_sums = root / _SHA256SUMS_NAME
    if not sha_sums.is_file():
        problems.append(f"{_SHA256SUMS_NAME} is missing")
    return problems


def assert_no_venv(tree: Path) -> list[str]:
    """Assert no ``.venv`` directory exists anywhere under [tree]."""
    return [
        str(path)
        for path in tree.rglob(".venv")
        if path.is_dir()
    ]


def copy_distribution(source: Path, destination: Path) -> None:
    """Copy a staged distribution tree without venv/pycache/dev leftovers."""
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ManifestError(f"source contains a symbolic link: {path}")
        if path.name in _SKIP_NAMES or path.suffix in _FORBIDDEN_SUFFIXES:
            continue
        if any(part in _FORBIDDEN_DIRS for part in path.relative_to(source).parts):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


_STAGED_RELATIVE_FILES = (
    "LICENSE",
    "requirements.lock",
    "requirements.txt",
)
_STAGED_DEPLOY_FILES = (
    "python-version.txt",
    "uv-version.txt",
    "uv-LICENSE-MIT",
    "release-metadata.json",
)
_STAGED_TREES = ("cc_remote", "web/dist")


def _copy_one_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ManifestError(f"required file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def stage_payload(source: Path, destination: Path) -> None:
    """Stage exactly the runtime tree a Windows install needs.

    ``destination`` becomes the ``payload`` of the release archive: the backend
    package, the combined lock, the canonical metadata, the pinned python/uv
    versions, the license, and the built web UI. Nothing machine-specific or
    development-only is copied.
    """
    source = source.resolve()
    destination = destination.resolve()
    if not (source / "cc_remote").is_dir():
        raise ManifestError("source has no cc_remote package directory")
    if not (source / "web" / "dist" / "index.html").is_file():
        raise ManifestError("web/dist/index.html is missing; build the web client first")
    for rel in _STAGED_RELATIVE_FILES:
        _copy_one_file(source / rel, destination / rel)
    for rel in _STAGED_DEPLOY_FILES:
        _copy_one_file(source / "deploy" / rel, destination / "deploy" / rel)
    for tree in _STAGED_TREES:
        copy_distribution(source / tree, destination / tree)


def _info_from_metadata(root: Path, git_sha: str, source_date_epoch: int) -> DistributionInfo:
    metadata_path = root / "deploy" / "release-metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read {metadata_path}") from exc
    required = ("distribution_version", "product_version", "protocol")
    if any(key not in metadata for key in required):
        raise ManifestError("release-metadata.json is missing canonical version fields")
    return DistributionInfo(
        distribution_version=metadata["distribution_version"],
        product_version=metadata["product_version"],
        protocol=int(metadata["protocol"]),
        git_sha=git_sha,
        source_date_epoch=source_date_epoch,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Windows distribution build/copy helpers")
    parser.add_argument("--build", metavar="ROOT", type=Path, help="build+write a manifest for ROOT")
    parser.add_argument("--copy", action="store_true", help="copy a distribution tree, filtering dev leftovers")
    parser.add_argument("--stage", action="store_true", help="stage the release payload from a source tree")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--git-sha", default="", help="40-char git SHA for the manifest (build)")
    parser.add_argument("--source-date-epoch", type=int, default=0, help="SOURCE_DATE_EPOCH for the manifest (build)")
    args = parser.parse_args(argv)

    if args.build is not None:
        info = _info_from_metadata(args.build, args.git_sha, args.source_date_epoch)
        write_manifest(args.build, build_manifest(args.build, info))
        return 0
    if args.copy:
        if not args.source or not args.destination:
            parser.error("--copy requires --source and --destination")
        copy_distribution(args.source, args.destination)
        return 0
    if args.stage:
        if not args.source or not args.destination:
            parser.error("--stage requires --source and --destination")
        stage_payload(args.source, args.destination)
        return 0
    parser.error("specify --build ROOT, --copy, or --stage")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
