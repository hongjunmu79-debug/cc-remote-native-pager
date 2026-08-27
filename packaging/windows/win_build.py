"""Deterministic assembly of the Windows release archive.

A release archive is a zip of ``setup.ps1``, the ``packaging/windows`` scripts
and the verified ``payload`` tree. Determinism here means: the same inputs
(bytes of every file) and the same SOURCE_DATE_EPOCH produce byte-identical
archives, so a downstream checksum is meaningful. Every entry gets a fixed
timestamp, sorted order, and normalized unix permissions (the Windows zip
extractor does not use them, but the archive is comparable across hosts).
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

from packaging.windows.win_manifest import DistributionInfo, build_manifest, write_manifest

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


def build_release_archive(
    *,
    setup_ps1: Path,
    packaging_dir: Path,
    payload: Path,
    output_path: Path,
    packaging_init: Path | None = None,
    source_date_epoch: int = 0,
) -> ReleaseArchive:
    """Zip a staged release into a deterministic archive.

    ``packaging_dir`` supplies the runtime scripts and PowerShell files that go
    into the archive root under ``packaging/windows/``. ``packaging_init`` is
    the ``packaging/__init__.py`` of the source tree, embedded as
    ``packaging/__init__.py`` so the extracted ``packaging`` is a regular
    package (never shadowed by the installed ``packaging`` distribution).
    ``payload`` is the verified distribution tree (already carrying
    ``distribution-manifest.json`` and ``SHA256SUMS``).
    """
    setup_ps1 = setup_ps1.resolve()
    packaging_dir = packaging_dir.resolve()
    payload = payload.resolve()
    if not setup_ps1.is_file():
        raise BuildError(f"setup.ps1 is missing: {setup_ps1}")
    if not packaging_dir.is_dir():
        raise BuildError(f"packaging directory is missing: {packaging_dir}")
    if not (payload / "distribution-manifest.json").is_file():
        raise BuildError(f"payload is not a built distribution: {payload}")

    manifest = json.loads((payload / "distribution-manifest.json").read_text(encoding="utf-8"))
    distribution_version = manifest["distribution_version"]
    product_version = manifest["product_version"]
    protocol = int(manifest["protocol"])

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    file_count = 0
    completed = False

    def _add_tree(archive: zipfile.ZipFile, tree: Path, prefix: str, *, mode: int) -> None:
        nonlocal file_count
        for path in sorted(tree.rglob("*")):
            relative = path.relative_to(tree).as_posix()
            arcname = f"{prefix}/{relative}"
            if path.is_dir():
                archive.writestr(_zip_info(arcname + "/", mode=0o755), b"")
            elif path.is_file():
                archive.writestr(_zip_info(arcname, mode=mode), path.read_bytes())
                file_count += 1
            else:
                raise BuildError(f"unsupported entry in {tree}: {path}")

    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            archive.writestr(_zip_info("setup.ps1", mode=0o755), setup_ps1.read_bytes())
            file_count += 1
            if packaging_init is not None:
                archive.writestr(
                    _zip_info("packaging/__init__.py", mode=0o644),
                    packaging_init.read_bytes(),
                )
                file_count += 1
            _add_tree(archive, packaging_dir, "packaging/windows", mode=0o755)
            _add_tree(archive, payload, "payload", mode=0o644)
        if source_date_epoch > 0:
            os.utime(temporary, (source_date_epoch, source_date_epoch))
        temporary.replace(output_path)
        completed = True
    finally:
        if not completed and temporary.exists():
            temporary.unlink(missing_ok=True)

    return ReleaseArchive(
        path=output_path,
        distribution_version=distribution_version,
        product_version=product_version,
        protocol=protocol,
        file_count=file_count,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the deterministic Windows release archive")
    parser.add_argument("--setup", type=Path, required=True, help="setup.ps1 to embed at the archive root")
    parser.add_argument("--packaging", type=Path, required=True, help="packaging/windows directory (scripts)")
    parser.add_argument("--packaging-init", type=Path, default=None, help="packaging/__init__.py of the source tree")
    parser.add_argument("--payload", type=Path, required=True, help="built payload tree")
    parser.add_argument("--output", type=Path, required=True, help="output zip path")
    parser.add_argument("--source-date-epoch", type=int, default=0)
    parser.add_argument("--git-sha", default="")
    args = parser.parse_args(argv)

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
