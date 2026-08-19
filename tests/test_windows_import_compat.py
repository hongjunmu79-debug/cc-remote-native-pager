"""Cross-platform import boundaries for optional POSIX-only features."""
from __future__ import annotations

import subprocess
import sys


def test_relay_and_wrapper_import_without_loading_posix_pty_modules():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import cc_remote.config; "
                "import cc_remote.wrapper.machine; "
                "import sys; "
                "assert 'cc_remote.claude_broker.session' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
