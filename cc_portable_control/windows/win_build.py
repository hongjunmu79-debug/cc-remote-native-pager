"""Deterministic assembly of the Windows release artifacts.

Two distinct deliverables are produced from one verified payload:

* **Portable archive** (``...-windows-x64-portable.zip``): extract anywhere and
  run from the extracted root via ``start-portable.ps1``, which creates its own
  runtime venv on first use (bundled ``uv.exe`` + ``requirements.lock``). It
  never registers scheduled tasks, a firewall rule, or registry values.
* **Installer archive** (``...-windows-x64-installer.zip``): the classic
  ``setup.ps1`` + ``cc_portable_control/windows`` + ``payload`` archive that
  the Inno Setup installer (``...-windows-x64-setup.exe``) embeds and
  invokes.

Determinism here means: the same inputs (bytes of every file) and the same
SOURCE_DATE_EPOCH produce byte-identical archives, so a downstream checksum is
meaningful. Every entry gets a fixed timestamp, sorted order, and normalized
unix permissions (the Windows zip extractor does not use them, but the archive
is comparable across hosts).
"""
from __future__ import annotations

import sys
from pathlib import Path

# See win_manifest.py: make the repository/release root importable when this
# module is run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json
import os
import zipfile
from dataclasses import dataclass

from cc_portable_control.windows.win_manifest import DistributionInfo, build_manifest, write_manifest

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class BuildError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseArchive:
    path: Path
    distribution_version: str
    product_version: str
    protocol: int
    file_count: int


def _zip_info(arcname: str, *, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # unix markers so cross-platform tools agree
    info.external_attr = (mode & 0xFFFF) << 16
    return info


def _resolve_distribution(payload: Path) -> tuple[Path, dict]:
    payload = payload.resolve()
    manifest_path = payload / "distribution-manifest.json"
    if not manifest_path.is_file():
        raise BuildError(f"payload is not a built distribution: {payload}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return payload, manifest


def _dist_values(manifest: dict) -> tuple[str, str, int]:
    return (
        manifest["distribution_version"],
        manifest["product_version"],
        int(manifest["protocol"]),
    )


def _write_zip(
    temporary: Path, root_entries: list[tuple[str, Path, int]], trees: list[tuple[Path, str, int]]
) -> int:
    """Write a deterministic zip. Returns the file count."""
    file_count = 0
    with zipfile.ZipFile(temporary, "w") as archive:
        for arcname, source, mode in root_entries:
            archive.writestr(_zip_info(arcname, mode=mode), source.read_bytes())
            file_count += 1
        for tree, prefix, mode in trees:
            for path in sorted(tree.rglob("*")):
                relative = path.relative_to(tree).as_posix()
                arcname = f"{prefix}/{relative}" if prefix else relative
                if path.is_dir():
                    archive.writestr(_zip_info(arcname + "/", mode=0o755), b"")
                elif path.is_file():
                    archive.writestr(_zip_info(arcname, mode=mode), path.read_bytes())
                    file_count += 1
                else:
                    raise BuildError(f"unsupported entry in {tree}: {path}")
    return file_count


def build_tree_archive(*, source: Path, output_path: Path, source_date_epoch: int = 0) -> int:
    """Pack one tree into a deterministic zip without adding a root prefix."""
    source = source.resolve()
    if not source.is_dir():
        raise BuildError(f"bundle source directory is missing: {source}")
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    completed = False
    try:
        file_count = _write_zip(temporary, [], [(source, "", 0o644)])
        if source_date_epoch > 0:
            os.utime(temporary, (source_date_epoch, source_date_epoch))
        temporary.replace(output_path)
        completed = True
        return file_count
    finally:
        if not completed and temporary.exists():
            temporary.unlink(missing_ok=True)


def build_release_archive(
    *,
    setup_ps1: Path,
    packaging_dir: Path,
    payload: Path,
    output_path: Path,
    packaging_init: Path | None = None,
    source_date_epoch: int = 0,
) -> ReleaseArchive:
    """Zip a staged release into the deterministic installer archive.

    The archive root carries ``setup.ps1`` (the installer entry point); under
    it live ``cc_portable_control/windows`` (runtime scripts and PowerShell files)
    and the verified ``payload`` tree. ``packaging_init`` is the
    ``cc_portable_control/__init__.py`` of the source tree, embedded as
    ``cc_portable_control/__init__.py`` so the extracted
    ``cc_portable_control`` is a regular package (not shadowed by the installed
    ``packaging`` distribution).
    """
    setup_ps1 = setup_ps1.resolve()
    packaging_dir = packaging_dir.resolve()
    payload, manifest = _resolve_distribution(payload)
    if not setup_ps1.is_file():
        raise BuildError(f"setup.ps1 is missing: {setup_ps1}")
    if not packaging_dir.is_dir():
        raise BuildError(f"cc_portable_control directory is missing: {packaging_dir}")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    completed = False
    try:
        root_entries: list[tuple[str, Path, int]] = [
            ("setup.ps1", setup_ps1, 0o755),
        ]
        if packaging_init is not None:
            root_entries.append(("cc_portable_control/__init__.py", packaging_init, 0o644))
        file_count = _write_zip(
            temporary,
            root_entries,
            [
                (packaging_dir, "cc_portable_control/windows", 0o755),
                (payload, "payload", 0o644),
            ],
        )
        if source_date_epoch > 0:
            os.utime(temporary, (source_date_epoch, source_date_epoch))
        temporary.replace(output_path)
        completed = True
    finally:
        if not completed and temporary.exists():
            temporary.unlink(missing_ok=True)

    distribution_version, product_version, protocol = _dist_values(manifest)
    return ReleaseArchive(
        path=output_path,
        distribution_version=distribution_version,
        product_version=product_version,
        protocol=protocol,
        file_count=file_count,
    )


def build_portable_archive(
    *,
    start_portable: Path,
    readme: Path | None,
    packaging_dir: Path,
    payload: Path,
    output_path: Path,
    packaging_init: Path | None = None,
    source_date_epoch: int = 0,
) -> ReleaseArchive:
    """Zip a staged release into the deterministic portable archive.

    The archive root carries ``start-portable.ps1`` (run from the extracted
    root; it creates a local runtime venv on first use and never mutates
    scheduled tasks/firewall/registry) plus the same
    ``cc_portable_control/windows`` and verified ``payload`` trees as the
    installer archive, without ``setup.ps1``.
    """
    start_portable = start_portable.resolve()
    packaging_dir = packaging_dir.resolve()
    payload, manifest = _resolve_distribution(payload)
    if not start_portable.is_file():
        raise BuildError(f"start-portable.ps1 is missing: {start_portable}")
    if not packaging_dir.is_dir():
        raise BuildError(f"cc_portable_control directory is missing: {packaging_dir}")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    completed = False
    try:
        root_entries: list[tuple[str, Path, int]] = [
            ("start-portable.ps1", start_portable, 0o755),
        ]
        if readme is not None:
            readme = readme.resolve()
            if not readme.is_file():
                raise BuildError(f"portable README is missing: {readme}")
            root_entries.append(("README-portable.txt", readme, 0o644))
        if packaging_init is not None:
            root_entries.append(("cc_portable_control/__init__.py", packaging_init, 0o644))
        file_count = _write_zip(
            temporary,
            root_entries,
            [
                (packaging_dir, "cc_portable_control/windows", 0o755),
                (payload, "payload", 0o644),
            ],
        )
        if source_date_epoch > 0:
            os.utime(temporary, (source_date_epoch, source_date_epoch))
        temporary.replace(output_path)
        completed = True
    finally:
        if not completed and temporary.exists():
            temporary.unlink(missing_ok=True)

    distribution_version, product_version, protocol = _dist_values(manifest)
    return ReleaseArchive(
        path=output_path,
        distribution_version=distribution_version,
        product_version=product_version,
        protocol=protocol,
        file_count=file_count,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the deterministic Windows release artifacts")
    parser.add_argument("--setup", type=Path, default=None, help="setup.ps1 to embed at the archive root (installer archive)")
    parser.add_argument("--start-portable", type=Path, default=None, help="start-portable.ps1 to embed at the archive root (portable archive)")
    parser.add_argument("--readme", type=Path, default=None, help="README-portable.txt to embed at the archive root (portable archive)")
    parser.add_argument("--packaging", type=Path, default=None, help="cc_portable_control/windows directory (scripts)")
    parser.add_argument("--packaging-init", type=Path, default=None, help="cc_portable_control/__init__.py of the source tree")
    parser.add_argument("--payload", type=Path, default=None, help="built payload tree")
    parser.add_argument("--output", type=Path, required=True, help="output zip path")
    parser.add_argument("--bundle-tree", type=Path, default=None, help="write one deterministic prefix-free tree zip and exit")
    parser.add_argument("--portable", action="store_true", help="assemble the portable archive instead of the installer archive")
    parser.add_argument("--source-date-epoch", type=int, default=0)
    parser.add_argument("--git-sha", default="")
    args = parser.parse_args(argv)

    if args.bundle_tree is not None:
        file_count = build_tree_archive(
            source=args.bundle_tree,
            output_path=args.output,
            source_date_epoch=args.source_date_epoch,
        )
        print(f"{args.output.resolve()} ({file_count} files)")
        return 0
    if args.payload is None or args.packaging is None:
        parser.error("release archive mode requires --payload and --packaging")

    metadata = json.loads(
        (args.payload / "deploy" / "release-metadata.json").read_text(encoding="utf-8")
    )
    info = DistributionInfo(
        distribution_version=metadata["distribution_version"],
        product_version=metadata["product_version"],
        protocol=int(metadata["protocol"]),
        git_sha=args.git_sha,
        source_date_epoch=args.source_date_epoch,
    )
    write_manifest(args.payload, build_manifest(args.payload, info))

    if args.portable:
        if args.start_portable is None:
            parser.error("--portable requires --start-portable")
        archive = build_portable_archive(
            start_portable=args.start_portable,
            readme=args.readme,
            packaging_dir=args.packaging,
            payload=args.payload,
            output_path=args.output,
            packaging_init=args.packaging_init,
            source_date_epoch=args.source_date_epoch,
        )
    else:
        if args.setup is None:
            parser.error("installer archive requires --setup")
        archive = build_release_archive(
            setup_ps1=args.setup,
            packaging_dir=args.packaging,
            payload=args.payload,
            output_path=args.output,
            packaging_init=args.packaging_init,
            source_date_epoch=args.source_date_epoch,
        )
    print(archive.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
