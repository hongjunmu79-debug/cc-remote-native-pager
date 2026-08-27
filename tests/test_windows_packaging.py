"""Zero-token tests for the reproducible Windows distribution.

These tests never touch the network, a model, a scheduled task, or a live
instance. They exercise the pure Python packaging modules and the clean-install
smoke suite against fabricated temp trees.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from packaging.windows import win_build, win_config, win_layout, win_manifest, win_smoke

METADATA = {
    "schema": 1,
    "product_version": "3.0.0",
    "distribution_version": "3.0.0-pager.5",
    "protocol": 19,
    "repository": {"owner": "hongjunmu79-debug", "name": "cc-remote-native-pager"},
    "android": {"application_id": "dev.ccremote.lan", "version_name": "3.0.0-pager.5", "version_code": 30014},
}


def make_source_tree(root: Path) -> None:
    """Create a minimal source tree that passes staging + verification.

    Idempotent: the same [root] may be staged twice (determinism tests).
    """
    (root / "cc_remote").mkdir(parents=True, exist_ok=True)
    (root / "cc_remote" / "__init__.py").write_text("__version__ = '3.0.0'\n", encoding="utf-8")
    (root / "cc_remote" / "protocol.py").write_text("PROTOCOL_VERSION = 19\n", encoding="utf-8")
    (root / "web" / "dist").mkdir(parents=True, exist_ok=True)
    (root / "web" / "dist" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (root / "requirements.lock").write_text("--universal --generate-hashes\n", encoding="utf-8")
    (root / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    deploy = root / "deploy"
    deploy.mkdir(parents=True, exist_ok=True)
    (deploy / "release-metadata.json").write_text(
        json.dumps(METADATA, sort_keys=True), encoding="utf-8"
    )
    (deploy / "python-version.txt").write_text("3.13.9\n", encoding="utf-8")
    (deploy / "uv-version.txt").write_text("0.11.16\n", encoding="utf-8")
    (deploy / "uv-LICENSE-MIT").write_text("MIT\n", encoding="utf-8")
    # Dev leftovers that must never reach the payload.
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_smoke.py").write_text("pass\n", encoding="utf-8")
    (root / "packaging").mkdir(exist_ok=True)
    (root / "packaging" / "dev-only.txt").write_text("x\n", encoding="utf-8")
    (root / "web" / "dist" / "__pycache__").mkdir(exist_ok=True)
    (root / "web" / "dist" / "__pycache__" / "x.pyc").write_bytes(b"\x00")


def build_fake_distribution(root: Path, *, git_sha: str = "0" * 40) -> None:
    """Build a valid distribution (manifest + checksums) under [root]."""
    info = win_manifest._info_from_metadata(root, git_sha, 0)
    win_manifest.write_manifest(root, win_manifest.build_manifest(root, info))


def make_distribution(dist: Path, *, git_sha: str = "0" * 40) -> Path:
    """Build a clean, verified distribution exactly like build.ps1 does.

    The raw source tree (with dev leftovers) is staged through
    ``stage_payload`` so the resulting distribution never contains
    ``__pycache__``/``.pyc`` — the same invariant the real pipeline relies on.
    """
    source = dist.parent / (dist.name + "_src")
    make_source_tree(source)
    win_manifest.stage_payload(source, dist)
    build_fake_distribution(dist, git_sha=git_sha)
    return dist


# ---------------------------------------------------------------------------
# win_config
# ---------------------------------------------------------------------------


def test_generate_secret_is_strong_hex():
    a = win_config.generate_secret()
    b = win_config.generate_secret()
    assert len(a) == 64
    assert int(a, 16)  # valid hex
    assert a != b
    with pytest.raises(ValueError):
        win_config.generate_secret(hex_bytes=4)


@pytest.mark.parametrize(
    "value",
    ["", "change-me", "changeme", "REPLACE_WITH_X", "your-secret", "xxx-secret", "password", "placeholder", "<fill-in>"],
)
def test_is_placeholder_rejects_weak_values(value):
    assert win_config.is_placeholder(value)


@pytest.mark.parametrize(
    "value",
    ["a-strong-16-char-password", "9d2f1c8a47e6b3d5a0c9f2e1d8b4a6c7"],
)
def test_is_placeholder_accepts_strong_values(value):
    assert not win_config.is_placeholder(value)


def test_validate_login_password():
    assert win_config.validate_login_password("a-strong-16-char-password") == []
    assert win_config.validate_login_password("short") != []
    assert win_config.validate_login_password("change-me-password-123") != []
    assert win_config.validate_login_password("with'quote-12345678") != []
    assert win_config.validate_login_password("with\\slash-12345678") != []
    assert win_config.validate_login_password("with\0control-12345678") != []
    assert win_config.validate_login_password("a" * 2000) != []


def test_validate_machine_name():
    assert win_config.validate_machine_name("desktop-1") == []
    assert win_config.validate_machine_name("DESKTOP_1.lan") == []
    assert win_config.validate_machine_name("default") != []
    assert win_config.validate_machine_name("localhost") != []
    assert win_config.validate_machine_name("has space") != []
    assert win_config.validate_machine_name("x" * 129) != []


def test_validate_workspace():
    assert win_config.validate_workspace("C:\\Users\\alice\\projects") == []
    assert win_config.validate_workspace("/home/alice/projects") == []
    assert win_config.validate_workspace("") != []
    assert win_config.validate_workspace("relative/path") != []
    assert win_config.validate_workspace("C:\\bad\x00path") != []


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("10.0.0.1", True),
        ("10.255.255.255", True),
        ("172.16.0.1", True),
        ("172.31.255.1", True),
        ("192.168.1.50", True),
        ("127.0.0.1", True),
        ("8.8.8.8", False),
        ("172.32.0.1", False),
        ("192.169.1.1", False),
        ("11.0.0.1", False),
        ("example.com", False),
        ("2001:db8::1", False),
        ("256.1.1.1", False),
        ("", False),
        (None, False),
    ],
)
def test_is_private_or_local_ip(host, expected):
    assert win_config.is_private_or_local_ip(host) is expected


def test_validate_public_origin():
    # HTTPS accepts any root origin.
    assert win_config.validate_public_origin(
        "https://remote.example.com", allow_insecure_http=False
    ) == []
    assert win_config.validate_public_origin(
        "https://192.168.1.50:8765", allow_insecure_http=False
    ) == []
    # Plain http is only allowed for private/local IP literals.
    assert win_config.validate_public_origin(
        "http://192.168.1.50:8765", allow_insecure_http=True
    ) == []
    assert win_config.validate_public_origin(
        "http://192.168.1.50:8765", allow_insecure_http=False
    ) != []
    assert win_config.validate_public_origin(
        "http://8.8.8.8:8765", allow_insecure_http=True
    ) != []
    assert win_config.validate_public_origin(
        "http://example.com", allow_insecure_http=True
    ) != []
    assert win_config.validate_public_origin(
        "http://localhost:8765", allow_insecure_http=True
    ) == []
    # Userinfo, paths, query, and fragments are rejected.
    assert win_config.validate_public_origin(
        "https://user:pass@example.com", allow_insecure_http=False
    ) != []
    assert win_config.validate_public_origin(
        "https://example.com/path", allow_insecure_http=False
    ) != []
    assert win_config.validate_public_origin(
        "https://example.com?q=1", allow_insecure_http=False
    ) != []
    assert win_config.validate_public_origin(
        "https://example.com#frag", allow_insecure_http=False
    ) != []


def test_dotenv_value_round_trips_windows_paths():
    value = r"C:\Users\alice\projects\my folder"
    quoted = win_config._dotenv_value(value)
    parsed = win_config.parse_env_file(f"KEY={quoted}\n")
    assert parsed["KEY"] == value
    assert win_config._dotenv_value("simple") == "simple"


def test_build_env_content_round_trips():
    answers = win_config.FirstRunAnswers(
        login_password="a-strong-16-char-password",
        machine_name="desktop-1",
        workspace=r"C:\Users\alice\projects",
        public_origin="http://192.168.1.50:8765",
        relay_port=8765,
        allow_insecure_http=True,
    )
    content = win_config.build_env_content(
        answers=answers,
        session_secret="s" * 64,
        wrapper_token="w" * 64,
        claude_bin=r"C:\Program Files\claude\claude.exe",
        codex_bin=None,
        state_dir=r"C:\Users\alice\cc-remote\state",
        work_root=r"C:\Users\alice\cc-remote\state\work",
        static_dir=r"C:\Users\alice\cc-remote\releases\current\web\dist",
    )
    env = win_config.parse_env_file(content)
    assert env["LOGIN_PASSWORD"] == "a-strong-16-char-password"
    assert env["SESSION_SECRET"] == "s" * 64
    assert env["WRAPPER_TOKEN"] == "w" * 64
    assert env["CC_REMOTE_MACHINE_ID"] == "desktop-1"
    assert env["CC_CWD"] == r"C:\Users\alice\projects"
    assert env["RELAY_URL"] == "ws://192.168.1.50:8765/ws"
    assert env["CLAUDE_BIN"] == r"C:\Program Files\claude\claude.exe"
    assert env["WEB_STATIC_DIR"] == r"C:\Users\alice\cc-remote\releases\current\web\dist"
    assert env["RELAY_HOST"] == "0.0.0.0"


def test_validate_preserved_config():
    good = win_config.build_env_content(
        answers=win_config.FirstRunAnswers(
            login_password="a-strong-16-char-password",
            machine_name="desktop-1",
            workspace=r"C:\Users\alice\projects",
            public_origin="http://192.168.1.50:8765",
            relay_port=8765,
            allow_insecure_http=True,
        ),
        session_secret="s" * 64,
        wrapper_token="w" * 64,
        claude_bin=None,
        codex_bin=None,
        state_dir="C:\\state",
        work_root="C:\\work",
    )
    assert win_config.validate_preserved_config(good) == []

    bad = good.replace("s" * 64, "REPLACE_WITH_s")
    assert win_config.validate_preserved_config(bad) != []

    missing = good.replace("WRAPPER_TOKEN=", "WRAPPER_TOKEN_OLD=")
    assert win_config.validate_preserved_config(missing) != []


def _run_win_config_cli(*args: str, **extra_env: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(Path(win_config.__file__)), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_win_config_cli_validate_answers():
    proc = _run_win_config_cli(
        "validate-answers",
        "--login-password", "a-strong-16-char-password",
        "--machine-name", "desktop-1",
        "--workspace", r"C:\Users\alice\projects",
        "--public-origin", "http://192.168.1.50:8765",
        "--relay-port", "8765",
        "--insecure",
    )
    assert proc.returncode == 0, proc.stderr

    proc = _run_win_config_cli(
        "validate-answers",
        "--login-password", "short",
        "--machine-name", "desktop-1",
        "--workspace", r"C:\Users\alice\projects",
        "--public-origin", "http://192.168.1.50:8765",
    )
    assert proc.returncode != 0


def test_win_config_cli_render_env(tmp_path: Path):
    proc = _run_win_config_cli(
        "render-env",
        "--login-password", "a-strong-16-char-password",
        "--machine-name", "desktop-1",
        "--workspace", r"C:\Users\alice\projects",
        "--public-origin", "http://192.168.1.50:8765",
        "--relay-port", "8765",
        "--insecure",
        "--state-dir", str(tmp_path / "state"),
        "--work-root", str(tmp_path / "work"),
        "--static-dir", str(tmp_path / "releases" / "current" / "web" / "dist"),
        CCW_SESSION_SECRET="s" * 64,
        CCW_WRAPPER_TOKEN="w" * 64,
    )
    assert proc.returncode == 0, proc.stderr
    assert "SESSION_SECRET=" + "s" * 64 in proc.stdout
    assert "RELAY_URL=ws://192.168.1.50:8765/ws" in proc.stdout
    assert "WEB_STATIC_DIR=" in proc.stdout


def test_win_config_cli_validate_preserved(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LOGIN_PASSWORD=a-strong-16-char-password\n"
        "SESSION_SECRET=" + "s" * 64 + "\n"
        "WRAPPER_TOKEN=" + "w" * 64 + "\n",
        encoding="utf-8",
    )
    proc = _run_win_config_cli("validate-preserved", "--file", str(env_path))
    assert proc.returncode == 0, proc.stderr

    env_path.write_text(
        "LOGIN_PASSWORD=REPLACE_WITH_me\n"
        "SESSION_SECRET=" + "s" * 64 + "\n"
        "WRAPPER_TOKEN=" + "w" * 64 + "\n",
        encoding="utf-8",
    )
    proc = _run_win_config_cli("validate-preserved", "--file", str(env_path))
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# win_layout
# ---------------------------------------------------------------------------


def test_install_layout_paths(tmp_path: Path):
    layout = win_layout.InstallLayout(tmp_path / "root")
    assert layout.config_file == tmp_path / "root" / "config" / ".env"
    layout.create_all()
    assert layout.config_dir.is_dir()
    assert layout.state_dir.is_dir()
    assert layout.releases_dir.is_dir()
    assert layout.runtime_dir.is_dir()
    assert layout.logs_dir.is_dir()


def test_default_install_root_uses_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    assert win_layout.default_install_root() == tmp_path / "appdata" / "cc-remote"


def test_acl_commands_restrict_to_principal_only():
    commands = win_layout.acl_commands(Path("C:\\x\\config"), "alice")
    assert len(commands) == 2
    assert commands[0] == ["icacls", "C:\\x\\config", "/inheritance:r"]
    assert commands[1][0:3] == ["icacls", "C:\\x\\config", "/grant:r"]
    assert commands[1][3].startswith("alice:")
    # No deny entry: a deny on BUILTIN\Users would lock the principal out.
    assert all("deny" not in command[2].lower() for command in commands)


def test_sha256_and_sha256sums(tmp_path: Path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"hello")
    digest = win_layout.sha256_of(path)
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    sums = win_layout.write_sha256sums(tmp_path, [path], relroot=tmp_path)
    assert sums.read_text(encoding="utf-8").strip() == f"{digest}  a.txt"


def test_is_absolute_windows_path_and_layout_leaf(tmp_path: Path):
    assert win_layout.is_absolute_windows_path("C:\\x")
    assert win_layout.is_absolute_windows_path(r"\\server\share")
    assert not win_layout.is_absolute_windows_path("x\\y")
    root = tmp_path / "root"
    root.mkdir()
    good = root / "a" / "b.txt"
    assert win_layout.validate_layout_leaf(good, root) == []
    bad = root / ".." / "escape.txt"
    assert win_layout.validate_layout_leaf(bad, root) != []
    bad2 = tmp_path / "outside.txt"
    assert win_layout.validate_layout_leaf(bad2, root) != []


# ---------------------------------------------------------------------------
# win_manifest
# ---------------------------------------------------------------------------


def test_build_manifest_and_verify_round_trip(tmp_path: Path):
    make_distribution(tmp_path, git_sha="a" * 40)
    assert win_manifest.verify_distribution(tmp_path) == []
    manifest = win_manifest.read_manifest(tmp_path)
    assert manifest["distribution_version"] == "3.0.0-pager.5"
    assert manifest["protocol"] == 19
    assert manifest["git_sha"] == "a" * 40
    assert "cc_remote/__init__.py" in manifest["files"]
    assert (tmp_path / "SHA256SUMS").is_file()


def test_verify_distribution_detects_corruption(tmp_path: Path):
    make_distribution(tmp_path)
    victim = tmp_path / "cc_remote" / "__init__.py"
    victim.write_text("corrupted", encoding="utf-8")
    problems = win_manifest.verify_distribution(tmp_path)
    assert any("checksum mismatch" in problem for problem in problems)
    victim.unlink()
    problems = win_manifest.verify_distribution(tmp_path)
    assert any("manifest file missing" in problem for problem in problems)


def test_rebuilding_manifest_over_built_distribution_is_idempotent(tmp_path: Path):
    # win_build.py regenerates the manifest per archive, and build.ps1 assembles
    # two archives (installer + portable) from ONE staged payload. Rebuilding
    # must not record stale self-checksums for SHA256SUMS / the manifest itself,
    # or the shipped artifact would fail the installer's payload gate.
    make_distribution(tmp_path)
    first = win_manifest.read_manifest(tmp_path)
    assert "SHA256SUMS" not in first["files"]
    assert "distribution-manifest.json" not in first["files"]

    info = win_manifest._info_from_metadata(tmp_path, "a" * 40, 0)
    for _ in range(3):  # more rebuilds than build.ps1 does; must stay clean
        win_manifest.write_manifest(tmp_path, win_manifest.build_manifest(tmp_path, info))
    assert win_manifest.verify_distribution(tmp_path) == []
    rebuilt = win_manifest.read_manifest(tmp_path)
    assert rebuilt["files"] == first["files"]
    assert "SHA256SUMS" not in rebuilt["files"]
    assert "distribution-manifest.json" not in rebuilt["files"]
    assert (tmp_path / "SHA256SUMS").is_file()


def test_verify_distribution_rejects_venv_and_symlinks(tmp_path: Path):
    make_distribution(tmp_path)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / ".venv").mkdir()
    problems = win_manifest.verify_distribution(tmp_path)
    assert any(".venv" in problem for problem in problems)


def test_assert_no_venv(tmp_path: Path):
    make_distribution(tmp_path)
    (tmp_path / "runtime" / ".venv").mkdir(parents=True)
    found = win_manifest.assert_no_venv(tmp_path)
    assert len(found) == 1
    assert found[0].endswith(".venv")


def test_stage_payload_filters_dev_leftovers(tmp_path: Path):
    source = tmp_path / "source"
    make_source_tree(source)
    destination = tmp_path / "payload"
    win_manifest.stage_payload(source, destination)
    assert (destination / "cc_remote" / "__init__.py").is_file()
    assert (destination / "web" / "dist" / "index.html").is_file()
    assert (destination / "deploy" / "release-metadata.json").is_file()
    assert (destination / "LICENSE").is_file()
    assert (destination / "requirements.lock").is_file()
    assert not (destination / "tests").exists()
    assert not (destination / "packaging").exists()
    assert not any(path.suffix == ".pyc" for path in destination.rglob("*"))


def test_stage_payload_requires_web_build(tmp_path: Path):
    source = tmp_path / "source"
    make_source_tree(source)
    (source / "web" / "dist" / "index.html").unlink()
    with pytest.raises(win_manifest.ManifestError):
        win_manifest.stage_payload(source, tmp_path / "payload")


def test_copy_distribution_filters_junk(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "keep.py").write_text("x\n", encoding="utf-8")
    (source / "junk.pyc").write_bytes(b"\x00")
    (source / ".DS_Store").write_bytes(b"\x00")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "skip.py").write_text("x\n", encoding="utf-8")
    dest = tmp_path / "dst"
    win_manifest.copy_distribution(source, dest)
    assert (dest / "keep.py").is_file()
    assert not (dest / "junk.pyc").exists()
    assert not (dest / ".DS_Store").exists()
    assert not (dest / "__pycache__").exists()


# ---------------------------------------------------------------------------
# win_smoke
# ---------------------------------------------------------------------------


def test_run_clean_install_smoke_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The default dev-path markers are the build machine's own home paths; on a
    # machine whose home is under one of them (e.g. the developer's box), every
    # config rendered to a temp dir would match. Isolate this clean-pass test
    # from the current username so it asserts "a clean render has no problems".
    monkeypatch.setattr(
        win_smoke,
        "FORBIDDEN_DEV_PATH_MARKERS",
        ("C:\\Users\\__definitely_not_a_real_user__", "/Users/__dev__"),
    )
    dist = make_distribution(tmp_path / "dist")
    problems = win_smoke.run_clean_install_smoke(dist, tmp_path / "smoke")
    assert problems == []


def test_smoke_refuses_placeholder_password(tmp_path: Path):
    dist = make_distribution(tmp_path / "dist")
    install_root = tmp_path / "install"
    with pytest.raises(win_smoke.SmokeFailure):
        win_smoke.render_first_run_env(
            dist_root=dist,
            install_root=install_root,
            login_password="change-me",
            machine_name="desktop-test",
            workspace=str(install_root / "workspace"),
            public_origin="http://192.168.1.50:8765",
        )


def test_smoke_rejects_public_http_origin(tmp_path: Path):
    dist = make_distribution(tmp_path / "dist")
    install_root = tmp_path / "install"
    with pytest.raises(win_smoke.SmokeFailure):
        win_smoke.render_first_run_env(
            dist_root=dist,
            install_root=install_root,
            login_password="a-strong-16-char-password",
            machine_name="desktop-test",
            workspace=str(install_root / "workspace"),
            public_origin="http://8.8.8.8:8765",
            allow_insecure_http=True,
        )


def test_smoke_rejects_forbidden_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dist = make_distribution(tmp_path / "dist")
    temp_root = tmp_path / "x"
    # The rendered config always embeds the install root (state/work/static
    # dirs), so a marker equal to that root must trip the dev-machine gate.
    marker = str(temp_root / "install")
    monkeypatch.setattr(win_smoke, "FORBIDDEN_DEV_PATH_MARKERS", (marker,))
    problems = win_smoke.run_clean_install_smoke(dist, temp_root)
    assert any("dev-machine path" in problem for problem in problems)


def test_smoke_rejects_old_lan_ip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The smoke gate exists to keep the old machine-specific default from ever
    # leaking into a fresh config.
    assert win_smoke.FORBIDDEN_OLD_LAN_IP == "192.168.3.4"  # cc-remote-scan-allow: fixture
    dist = make_distribution(tmp_path / "dist")
    # Reroute the default origin the smoke suite renders so it carries the
    # forbidden old IP, and prove the gate flags it.
    monkeypatch.setattr(
        win_smoke,
        "render_first_run_env",
        _render_with_origin(win_smoke, "http://192.168.3.4:8765"),  # cc-remote-scan-allow: fixture
    )
    problems = win_smoke.run_clean_install_smoke(dist, tmp_path / "y")
    assert any("old machine-specific LAN IP" in problem for problem in problems)


def _render_with_origin(smoke_module, origin: str):
    import functools

    original = smoke_module.render_first_run_env

    @functools.wraps(original)
    def wrapped(**kwargs):
        kwargs["public_origin"] = origin
        return original(**kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# win_build
# ---------------------------------------------------------------------------


def _stage_and_build_archive(tmp_path: Path, epoch: int, name: str = "cc-remote-v3.0.0-pager.5-windows-x64.zip") -> tuple[Path, win_build.ReleaseArchive]:
    # Each call gets an isolated source+payload so a build never inherits a
    # previous call's distribution-manifest.json/SHA256SUMS — the same
    # guarantee build.ps1 gives by removing its staging root between runs.
    stem = Path(name).stem
    source = tmp_path / f"source-{stem}"
    make_source_tree(source)
    payload = tmp_path / f"payload-{stem}"
    win_manifest.stage_payload(source, payload)
    build_fake_distribution(payload)
    packaging = tmp_path / "packaging"
    (packaging / "windows").mkdir(parents=True, exist_ok=True)
    (packaging / "__init__.py").write_text('"""pkg"""\n', encoding="utf-8")
    (packaging / "windows" / "install.ps1").write_text("# install\n", encoding="utf-8")
    (packaging / "windows" / "win_config.py").write_text("import json\n", encoding="utf-8")
    setup = tmp_path / "setup.ps1"
    setup.write_text("# setup\n", encoding="utf-8")
    output = tmp_path / "out" / name
    archive = win_build.build_release_archive(
        setup_ps1=setup,
        packaging_dir=packaging / "windows",
        payload=payload,
        output_path=output,
        packaging_init=packaging / "__init__.py",
        source_date_epoch=epoch,
    )
    return source, archive


def test_build_release_archive_is_deterministic(tmp_path: Path):
    _, first = _stage_and_build_archive(tmp_path, 0, name="a.zip")
    _, second = _stage_and_build_archive(tmp_path, 0, name="b.zip")
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.distribution_version == "3.0.0-pager.5"
    assert first.protocol == 19
    assert first.path.suffix == ".zip"


def test_build_release_archive_epoch_stamps_mtime_not_bytes(tmp_path: Path):
    # Archive bytes are deterministic regardless of SOURCE_DATE_EPOCH; the
    # epoch is stamped on the outer file so downstream tools can set the date.
    _, first = _stage_and_build_archive(tmp_path, 0, name="a.zip")
    _, second = _stage_and_build_archive(tmp_path, 1234567890, name="b.zip")
    assert first.path.read_bytes() == second.path.read_bytes()
    assert int(first.path.stat().st_mtime) != 1234567890
    assert int(second.path.stat().st_mtime) == 1234567890


def test_build_release_archive_contents(tmp_path: Path):
    import zipfile

    _, archive = _stage_and_build_archive(tmp_path, 0)
    with zipfile.ZipFile(archive.path) as handle:
        names = set(handle.namelist())
        assert "setup.ps1" in names
        assert "packaging/__init__.py" in names
        assert "packaging/windows/install.ps1" in names
        assert "payload/distribution-manifest.json" in names
        assert "payload/cc_remote/__init__.py" in names
        assert "payload/web/dist/index.html" in names


# ---------------------------------------------------------------------------
# Installed runtime wiring (static guards on the shipped PowerShell)
# ---------------------------------------------------------------------------


def _repo_packaging_script(name: str) -> str:
    path = Path(__file__).resolve().parents[1] / "packaging" / "windows" / name
    return path.read_text(encoding="utf-8")


def test_installer_wires_cc_remote_import_path_for_the_venv():
    # The runtime venv installs only third-party deps (requirements.lock), so
    # nothing makes `python -m cc_remote.relay` find the app package unless the
    # installer puts the current release on the venv's sys.path. The release
    # root IS the payload content (win_manifest --copy copies payload/* into
    # releases\<version>/*), so the .pth must point at the junction itself,
    # which retargets on upgrade and rollback. The .pth mechanism keeps
    # packaging/ out of the app process (no shadowing of the venv's installed
    # `packaging` distribution).
    script = _repo_packaging_script("install.ps1")
    assert "cc_remote_release.pth" in script
    assert "Lib\\site-packages" in script
    assert 'Join-Path $releasesDir "current"' in script
    assert "current\\payload" not in script and "current/payload" not in script


def test_supervisor_and_start_invoke_python_m_modules():
    # Both the scheduled-task supervisor and the portable foreground launcher
    # run `python -m cc_remote.<module>`, which is exactly what the .pth wiring
    # makes importable. Guard that the module names stay in sync.
    supervise = _repo_packaging_script("supervise.ps1")
    assert "-m cc_remote.relay" in supervise or "cc_remote.relay" in supervise
    assert "cc_remote.wrapper" in supervise
    start = _repo_packaging_script("start.ps1")
    assert "cc_remote.relay" in start
    assert "cc_remote.wrapper" in start


def test_payload_never_contains_the_packaging_package():
    # If a staged payload ever carried a top-level `packaging/` dir, the app's
    # sys.path (which includes the current release via the .pth) could shadow
    # the venv's installed `packaging` distribution and break deps that import
    # packaging.version (e.g. pydantic). stage_payload must keep it out.
    import shutil
    import tempfile

    source = Path(tempfile.mkdtemp())
    dest = Path(tempfile.mkdtemp())
    try:
        make_source_tree(source)
        (source / "packaging" / "__init__.py").write_text(
            "import packaging.version\n", encoding="utf-8"
        )
        win_manifest.stage_payload(source, dest)
        assert not (dest / "packaging").exists()
        assert (dest / "cc_remote").is_dir()
    finally:
        shutil.rmtree(source, ignore_errors=True)
        shutil.rmtree(dest, ignore_errors=True)


# ---------------------------------------------------------------------------
# Portable archive (the second Windows deliverable)
# ---------------------------------------------------------------------------


def _stage_and_build_portable_archive(
    tmp_path: Path,
    epoch: int,
    name: str = "cc-remote-v3.0.0-pager.5-windows-x64-portable.zip",
) -> tuple[Path, win_build.ReleaseArchive]:
    stem = Path(name).stem
    source = tmp_path / f"source-{stem}"
    make_source_tree(source)
    payload = tmp_path / f"payload-{stem}"
    win_manifest.stage_payload(source, payload)
    build_fake_distribution(payload)
    packaging = tmp_path / "packaging"
    (packaging / "windows").mkdir(parents=True, exist_ok=True)
    (packaging / "__init__.py").write_text('"""pkg"""\n', encoding="utf-8")
    (packaging / "windows" / "install.ps1").write_text("# install\n", encoding="utf-8")
    (packaging / "windows" / "win_config.py").write_text("import json\n", encoding="utf-8")
    start_portable = tmp_path / "start-portable.ps1"
    start_portable.write_text("# start-portable\n", encoding="utf-8")
    readme = tmp_path / "README-portable.txt"
    readme.write_text("# portable\n", encoding="utf-8")
    output = tmp_path / "out" / name
    archive = win_build.build_portable_archive(
        start_portable=start_portable,
        readme=readme,
        packaging_dir=packaging / "windows",
        payload=payload,
        output_path=output,
        packaging_init=packaging / "__init__.py",
        source_date_epoch=epoch,
    )
    return source, archive


def test_build_portable_archive_contents(tmp_path: Path):
    import zipfile

    _, archive = _stage_and_build_portable_archive(tmp_path, 0)
    with zipfile.ZipFile(archive.path) as handle:
        names = set(handle.namelist())
        assert "start-portable.ps1" in names
        assert "README-portable.txt" in names
        assert "packaging/__init__.py" in names
        assert "packaging/windows/install.ps1" in names
        assert "payload/distribution-manifest.json" in names
        assert "payload/cc_remote/__init__.py" in names
        assert "payload/web/dist/index.html" in names
        # A portable archive is run from the extracted root, never installed:
        # it must not carry the installer entry point.
        assert "setup.ps1" not in names


def test_portable_archive_is_deterministic_and_named_from_metadata(tmp_path: Path):
    # Determinism: two builds from the same epoch are byte-identical.
    _, first = _stage_and_build_portable_archive(tmp_path, 0, name="a.zip")
    _, second = _stage_and_build_portable_archive(tmp_path, 0, name="b.zip")
    assert first.path.read_bytes() == second.path.read_bytes()
    # The archive carries the canonical version values from release-metadata.json.
    assert first.distribution_version == "3.0.0-pager.5"
    assert first.product_version == "3.0.0"
    # build.ps1 derives the artifact name from distribution_version.
    default = _stage_and_build_portable_archive(tmp_path, 0)
    assert default[1].path.name == "cc-remote-v3.0.0-pager.5-windows-x64-portable.zip"


def test_portable_archive_requires_start_portable(tmp_path: Path):
    source = tmp_path / "source"
    make_source_tree(source)
    payload = tmp_path / "payload"
    win_manifest.stage_payload(source, payload)
    build_fake_distribution(payload)
    packaging = tmp_path / "packaging"
    (packaging / "windows").mkdir(parents=True, exist_ok=True)
    with pytest.raises(win_build.BuildError):
        win_build.build_portable_archive(
            start_portable=tmp_path / "missing.ps1",
            readme=None,
            packaging_dir=packaging / "windows",
            payload=payload,
            output_path=tmp_path / "out.zip",
        )


# ---------------------------------------------------------------------------
# Installed runtime wiring (static guards on the shipped PowerShell)
# ---------------------------------------------------------------------------


def _repo_file(relpath: str) -> str:
    return (Path(__file__).resolve().parents[1] / relpath).read_text(encoding="utf-8")


def test_start_portable_bootstraps_venv_and_delegates():
    # start-portable.ps1 is the portable root entry: it must create a private
    # venv from the bundled uv + pinned lock, wire the payload into the venv
    # import path, run the config wizard with a portable static dir, then
    # delegate to the shared start.ps1.
    script = _repo_packaging_script("start-portable.ps1")
    assert "bin\\uv.exe" in script
    assert "python-version.txt" in script
    assert "requirements.lock" in script
    assert "cc_remote_portable.pth" in script
    assert "config-first-run.ps1" in script
    assert "-StaticDir" in script
    assert 'payload "web\\dist"' in script or 'payload\\web\\dist' in script
    assert "runtime\\.venv" in script
    assert "Scripts\\python.exe" in script
    assert "start.ps1" in script


def test_config_first_run_accepts_static_dir():
    script = _repo_packaging_script("config-first-run.ps1")
    assert "[string]$StaticDir" in script
    assert '"--static-dir", $StaticDir' in script


def test_inno_installer_is_a_real_installer_and_build_fails_closed():
    # The .iss must produce a genuine installer: it extracts setup.ps1 + the
    # packaging scripts + the verified payload and RUNS setup.ps1, so the exe
    # can never drift from install.ps1. The compiler wrapper fails (never
    # fakes) when ISCC.exe is absent.
    iss = _repo_file("packaging/windows/inno/cc-remote.iss")
    assert "[Setup]" in iss
    assert "OutputBaseFilename={#OutputName}" in iss
    assert "DistVersion" in iss
    assert 'Source: "{#StageDir}\\setup.ps1"' in iss
    assert 'Source: "{#StageDir}\\packaging\\*"' in iss
    assert 'Source: "{#StageDir}\\payload\\*"' in iss
    assert 'Filename: "powershell.exe"' in iss
    assert "setup.ps1" in iss
    assert "-InstallRoot" in iss
    builder = _repo_packaging_script("build-installer.ps1")
    assert "ISCC.exe" in builder
    assert "Refusing to emit a fake installer" in builder
    assert "-NoServices" in builder


def test_start_ps1_runs_both_concurrently_and_cleans_up():
    # Portable `-Service both` must launch relay and wrapper as concurrent
    # tracked children (Start-Process, not the blocking `&`), wait for the
    # first to exit, and stop the remaining one so no orphan survives.
    script = _repo_packaging_script("start.ps1")
    assert "Start-Process" in script
    assert "-NoNewWindow" in script
    assert "cc_remote.relay" in script
    assert "cc_remote.wrapper" in script
    assert "Wait-FirstChildExit" in script
    assert "Stop-Process" in script
    # The pre-fix blocking form must be gone.
    assert "& $venvPython -m cc_remote.relay" not in script
    assert "& $venvPython -m cc_remote.wrapper" not in script


def test_install_uses_shared_register_tasks_helper():
    script = _repo_packaging_script("install.ps1")
    assert "register-tasks.ps1" in script
    # A real install never unregisters tasks.
    assert "Unregister-ScheduledTask" not in script


def test_uninstall_rollback_is_transactional():
    # -Rollback must re-sync the venv to the previous release BEFORE switching
    # the junction, re-create the tasks via the shared helper, start them, and
    # health-check. A real uninstall unregisters the tasks only AFTER the
    # rollback branch, so a rollback never finds its tasks already gone. The
    # failure-safe restore helper re-switches the junction back.
    script = _repo_packaging_script("uninstall.ps1")
    lines = script.splitlines()

    def find(sub: str) -> int:
        for index, line in enumerate(lines):
            if sub in line:
                return index
        raise AssertionError(f"missing {sub!r} in uninstall.ps1")

    assert find("pip install") < find("-Target $prevDir")
    assert find("register-tasks.ps1") < find("Test-SupervisedHealth")
    assert find("if ($Rollback)") < find("Unregister-ScheduledTask")
    assert "Restore-ActiveRelease" in script
    assert "Test-SupervisedHealth" in script


def test_build_ps1_produces_three_artifacts():
    # One verified payload yields the installer zip, the portable zip, and the
    # compiled installer exe — all named from the canonical distribution
    # version from deploy/release-metadata.json.
    script = _repo_packaging_script("build.ps1")
    assert "windows-x64.zip" in script
    assert "windows-x64-portable.zip" in script
    assert "windows-x64-setup.exe" in script
    assert "--portable" in script
    assert "--start-portable" in script
    assert "build-installer.ps1" in script


# ---------------------------------------------------------------------------
# Real dual-process contract test (Windows only; isolated temp install)
# ---------------------------------------------------------------------------

_REAL_START_REASON = (
    "start.ps1 -Service both is a Windows PowerShell behavior; "
    "exercised against a temp install root on the Windows CI runner"
)


def _write_stub_module(site_packages: Path, module: str, body: str) -> None:
    package = site_packages / "cc_remote"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / f"{module}.py").write_text(body, encoding="utf-8")


def _count_venv_python_processes(venv_python: Path) -> int:
    proc = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{venv_python}*' }}).Count",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return -1


def _terminate_venv_python_processes(venv_python: Path) -> None:
    subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{venv_python}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(not sys.platform.startswith("win"), reason=_REAL_START_REASON)
def test_start_ps1_service_both_launches_concurrently_and_cleans_up(tmp_path: Path):
    # Real dual-process contract: start.ps1 -Service both must launch relay AND
    # wrapper at the same time (relay must not block wrapper startup), keep both
    # alive while they run, and exit 0 after they finish with no orphan python.
    import time

    install_root = tmp_path / "install"
    venv_dir = install_root / "runtime" / ".venv"
    venv_python = venv_dir / "Scripts" / "python.exe"
    config_dir = install_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("RELAY_PORT=8765\n", encoding="utf-8")

    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert created.returncode == 0, created.stderr
    assert venv_python.is_file()

    site_packages = venv_dir / "Lib" / "site-packages"
    relay_marker = tmp_path / "relay.started"
    wrapper_marker = tmp_path / "wrapper.started"
    _write_stub_module(
        site_packages,
        "relay",
        f"import time\nopen({str(relay_marker)!r}, 'w').write('relay')\ntime.sleep(10)\n",
    )
    _write_stub_module(
        site_packages,
        "wrapper",
        f"import time\nopen({str(wrapper_marker)!r}, 'w').write('wrapper')\ntime.sleep(10)\n",
    )

    start_ps1 = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "start.ps1"
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(start_ps1), "-Service", "both", "-InstallRoot", str(install_root),
    ]
    # Run from a directory that carries no cc_remote package, so the fake
    # venv's stub modules are authoritative (the repo root would shadow them).
    proc = subprocess.Popen(cmd, cwd=str(install_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 25
        both_seen_while_alive = False
        while time.time() < deadline:
            if relay_marker.exists() and wrapper_marker.exists():
                if proc.poll() is None:
                    both_seen_while_alive = True
                    break
            time.sleep(0.2)
        stdout, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    assert both_seen_while_alive, (
        "relay and wrapper were not both running at the same time; "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    assert proc.returncode == 0, f"stdout={stdout!r} stderr={stderr!r}"
    assert _count_venv_python_processes(venv_python) == 0, "orphan python process left behind"
    _terminate_venv_python_processes(venv_python)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason=_REAL_START_REASON)
def test_start_ps1_service_both_propagates_failure_and_stops_the_other(tmp_path: Path):
    # Failure variant: when the relay exits non-zero, start.ps1 must propagate
    # that exit code AND stop the still-running wrapper (no orphan).

    install_root = tmp_path / "install-fail"
    venv_dir = install_root / "runtime" / ".venv"
    venv_python = venv_dir / "Scripts" / "python.exe"
    config_dir = install_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("RELAY_PORT=8765\n", encoding="utf-8")

    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert created.returncode == 0, created.stderr

    site_packages = venv_dir / "Lib" / "site-packages"
    relay_marker = tmp_path / "relay-fail.started"
    wrapper_marker = tmp_path / "wrapper-fail.started"
    _write_stub_module(
        site_packages,
        "relay",
        f"import time, sys\nopen({str(relay_marker)!r}, 'w').write('relay')\ntime.sleep(1)\nsys.exit(7)\n",
    )
    # The wrapper would run for 30s if it were not stopped by start.ps1.
    _write_stub_module(
        site_packages,
        "wrapper",
        f"import time\nopen({str(wrapper_marker)!r}, 'w').write('wrapper')\ntime.sleep(30)\n",
    )

    start_ps1 = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "start.ps1"
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(start_ps1), "-Service", "both", "-InstallRoot", str(install_root),
    ]
    # Run from a directory that carries no cc_remote package so the stubs win.
    proc = subprocess.run(cmd, cwd=str(install_root), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 7, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert wrapper_marker.exists(), "wrapper never launched before the relay failed"
    # The wrapper's 30s sleep must have been interrupted; nothing may linger.
    assert _count_venv_python_processes(venv_python) == 0, "orphan wrapper python left behind"
    _terminate_venv_python_processes(venv_python)
