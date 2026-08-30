cc-remote portable distribution
================================

This folder is the complete, self-contained cc-remote package. Run it
directly from wherever you extracted it — nothing is installed, nothing is
registered, and no scheduled tasks, firewall rules, or registry values are
created.

Quick start
-----------

    1. Extract this archive to any folder, e.g. C:\tools\cc-remote.
    2. Right-click start-portable.ps1 -> "Run with PowerShell", or run:

           powershell -NoProfile -ExecutionPolicy Bypass -File start-portable.ps1

    3. On the very first run you will be asked for the LAN settings. A web
       login password is an optional fallback; normal onboarding uses the
       one-time QR shown by the local Web console. The wizard also creates a
       private runtime venv under runtime\.venv using the bundled uv.exe and
       the pinned requirements.lock — this is the only step that needs network
       access.

The web UI opens at http://<this-machine-lan-ip>:8765. Open it locally, display
the pairing QR, then scan it from the Android pager.

What it runs
------------

    * Relay (WebSocket relay + web UI) on TCP 8765 by default.
    * Wrapper (Claude/Codex session broker) on the same machine.
    Use -Service relay or -Service wrapper to run only one of them.

Uninstalling
------------

Delete this folder. There is nothing else to undo.

Notes and limitations
---------------------

    * First run downloads the pinned Python interpreter and the locked wheels
      once; everything after that is offline.
    * The relay/wrapper stay in the foreground of your console. Ctrl+C stops
      both. To run unsupervised, install the installer distribution instead
      (cc-remote-...-setup.exe), which registers supervised scheduled tasks.
    * The password and all generated secrets live in config\.env inside this
      folder, restricted to the user who created them.
    * This portable build does not open a firewall rule. For LAN access from
      other devices you may need to allow TCP 8765 through Windows Defender
      Firewall yourself, or use the installer distribution which adds a
      LocalSubnet-scoped rule.
