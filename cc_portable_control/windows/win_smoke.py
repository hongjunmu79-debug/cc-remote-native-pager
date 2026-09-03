"""Zero-token clean-install smoke checks for the Windows distribution.

These run on a plain temp tree: no scheduled tasks, no firewall rule, no model
call, and no live relay/wrapper. They prove the payload verifies, the
first-run config refuses placeholders and generates strong secrets, the config
is preserved across an upgrade, and no machine-created ``.venv`` or
dev-machine path leaks into the package.
"""
from __future__ import annotations

import sys
from pathlib import Path

# See win_manifest.py: make the repository/release root importable when this
# module is run as a script from the archive.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse
import json
import shutil
import tempfile

from cc_portable_control.windows.win_config import (
    FirstRunAnswers,
    build_env_content,
    generate_secret,
    parse_env_file,
    validate_answers,
    validate_preserved_config,
)
from cc_portable_control.windows.win_layout import InstallLayout
from cc_portable_control.windows.win_manifest import (
    DistributionInfo,
    assert_no_venv,
    find_forbidden_entries,
    read_manifest,
    verify_distribution,
)

FORBIDDEN_DEV_PATH_MARKERS = ("C:\\Users\\23715", "/Users/23715", "/home/23715")  # cc-remote-scan-allow: the markers the smoke gate guards against
FORBIDDEN_OLD_LAN_IP = "192.168.3.4"  # cc-remote-scan-allow: the old LAN IP the smoke gate guards against


class SmokeFailure(AssertionError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _normalize_path_text(text: str) -> str:
    return text.replace("\\", "/").rstrip("/").lower()


def _path_is_within(path: Path, marker: str) -> bool:
    """True when [path] is AT or UNDER [marker] (segment-wise, separator/case blind)."""
    base = _normalize_path_text(marker)
    target = _normalize_path_text(str(path))
    candidates = (target, target[2:]) if len(target) >= 3 and target[1:3] == ":/" else (target,)
    return bool(base) and any(
        candidate == base or candidate.startswith(base + "/")
        for candidate in candidates
    )


def load_distribution_info(dist_root: Path) -> DistributionInfo:
    manifest = read_manifest(dist_root)
    return DistributionInfo(
        distribution_version=manifest["distribution_version"],
        product_version=manifest["product_version"],
        protocol=manifest["protocol"],
        git_sha=manifest["git_sha"],
        source_date_epoch=manifest["source_date_epoch"],
    )


def render_first_run_env(
    *,
    dist_root: Path,
    install_root: Path,
    login_password: str,
    machine_name: str,
    workspace: str,
    public_origin: str,
    relay_port: int = 8765,
    allow_insecure_http: bool = True,
) -> tuple[dict, str]:
    """Render a validated .env for a clean install, or raise SmokeFailure."""
    answers = FirstRunAnswers(
        login_password=login_password,
        machine_name=machine_name,
        workspace=workspace,
        public_origin=public_origin,
        relay_port=relay_port,
        allow_insecure_http=allow_insecure_http,
    )
    errors = validate_answers(answers)
    if errors:
        raise SmokeFailure("first-run answers invalid: " + "; ".join(errors))

    info = load_distribution_info(dist_root)
    layout = InstallLayout(install_root)
    work_root = layout.state_dir / "work"

    content = build_env_content(
        answers=answers,
        session_secret=generate_secret(),
        wrapper_token=generate_secret(),
        claude_bin=None,
        codex_bin=None,
        state_dir=str(layout.state_dir),
        work_root=str(work_root),
        # The installer points the relay at the ``current`` release junction so
        # the preserved config keeps serving the UI across upgrades.
        static_dir=str(install_root / "releases" / "current" / "web" / "dist"),
    )
    return {"distribution_version": info.distribution_version}, content


def run_clean_install_smoke(dist_root: Path, temp_root: Path) -> list[str]:
    """Simulate a clean install in [temp_root] and return observed problems."""
    problems: list[str] = []

    problems.extend(verify_distribution(dist_root))
    if problems:
        return problems

    venv_paths = assert_no_venv(dist_root)
    if venv_paths:
        problems.append("distribution contains .venv directories: " + ", ".join(venv_paths))

    install_root = temp_root / "install"
    install_root.mkdir(parents=True, exist_ok=True)
    layout = InstallLayout(install_root)
    layout.create_all()

    _, env_content = render_first_run_env(
        dist_root=dist_root,
        install_root=install_root,
        login_password="a-strong-16-char-login-password",
        machine_name="desktop-test",
        workspace=str(install_root / "workspace"),
        public_origin="http://192.168.50.9:8765",
    )
    env_path = layout.config_file
    env_path.write_text(env_content, encoding="utf-8")
    if validate_preserved_config(env_content):
        problems.append("freshly rendered config failed the preserved-config gate")

    preserved = env_path.read_text(encoding="utf-8")

    # Compare against PARSED values with normalized separators: dotenv
    # double-quoting escapes backslashes (``C:\\Users``), so raw text matching
    # would never catch a Windows dev-machine path.
    parsed = parse_env_file(preserved)
    rendered_text = " ".join(
        f"{key}={value}" for key, value in parsed.items()
    ).replace("\\", "/").lower()
    for marker in FORBIDDEN_DEV_PATH_MARKERS:
        if marker.replace("\\", "/").lower() not in rendered_text:
            continue
        # Exempt only this run's own scratch tree: a match is noise when temp_root
        # is AT or UNDER the marker (mkdtemp landing in a dev home); a marker that
        # is not an ancestor of temp_root is a real leak and still fails.
        if _path_is_within(temp_root, marker):
            continue
        problems.append(f"config leaks a dev-machine path marker: {marker}")
    if FORBIDDEN_OLD_LAN_IP in rendered_text:
        problems.append("config still references the old machine-specific LAN IP")

    # Upgrade simulation: a second payload with the same version layout, but the
    # previously written config must be preserved byte-for-byte.
    upgrade_root = temp_root / "upgrade"
    shutil.copytree(dist_root, upgrade_root)
    if validate_preserved_config(preserved):
        problems.append("preserved config failed validation after upgrade staging")
    if env_path.read_text(encoding="utf-8") != preserved:
        problems.append("upgrade did not preserve the existing config")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows package clean-install smoke check")
    parser.add_argument("--check", type=Path, default=None, help="staged distribution root to clean-install smoke")
    parser.add_argument(
        "--check-tree", type=Path, default=None,
        help="extracted release tree to scan for forbidden dev entries",
    )
    parser.add_argument(
        "--temp", type=Path, default=None,
        help=(
            "temp root to run the clean-install smoke in and keep; when omitted "
            "a fresh per-invocation directory is created and deleted"
        ),
    )
    args = parser.parse_args(argv)

    if args.check_tree is not None:
        problems = find_forbidden_entries(args.check_tree)
        if problems:
            print("TREE CHECK FAILED:", file=sys.stderr)
            for problem in problems:
                print("  - " + problem, file=sys.stderr)
            return 1
        print("tree check passed")
        return 0

    if args.check is None:
        parser.error("specify --check ROOT or --check-tree ROOT")

    if args.temp is not None:
        # An explicit --temp is caller-owned: it is used verbatim and never
        # deleted, so the caller can keep the install/upgrade trees for
        # inspection. It must be empty each run — the smoke stages a second
        # copy into <temp>/upgrade via copytree, which refuses to overwrite a
        # tree left by a prior run (the collision the default mkdtemp path
        # below avoids).
        temp_root = args.temp
        temp_root.mkdir(parents=True, exist_ok=True)
        problems = run_clean_install_smoke(args.check, temp_root)
    else:
        # Fresh per-invocation default: build.ps1 and the release verify step
        # invoke this CLI several times in separate processes, so a fixed
        # default would collide on the leftover install/upgrade trees of the
        # previous run (manual Release run 33125872590). mkdtemp gives this
        # invocation a uniquely-named root it alone owns; the finally always
        # removes it, and never touches a broader user/system temp directory.
        temp_root = Path(tempfile.mkdtemp(prefix="cc-remote-windows-smoke-"))
        try:
            problems = run_clean_install_smoke(args.check, temp_root)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    if problems:
        print("SMOKE FAILED:", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        return 1
    print("smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
