# 3.0.0-pager.13

Windows now opens a persistent desktop control window. It shows service and
client status, launches a dedicated QR page, and offers start/restart, network
diagnosis, LAN firewall repair, logs, and the standard Windows uninstaller.
Failure to start the service or default browser remains visible with a recovery
message. No password or pairing credential is written into a URL or diagnostic
report.

The installer registers in Windows Installed Apps and ships a real uninstaller.
Uninstall removes this installation's tasks, runtime and releases after the
user confirms, while keeping configuration, state and logs for reinstall.
Shortcuts are replaced on upgrade. Windows x64-compatible ARM64 installations
are accepted; the runtime remains x64 and uses Windows emulation.

The QR page is available regardless of previous browser login. Android exposes
re-pairing on the empty dashboard and can decode a QR from the system image
picker as well as from the camera.

Services can run on battery. Supervised Python stderr no longer terminates the
PowerShell 5.1 supervisor. Installation paths containing Chinese characters use
a UTF-8 Python package path.

Network requirements remain: the phone must be able to reach the Windows
guest/host on the LAN. Guest Wi-Fi isolation, virtual-machine NAT, or denied
firewall permission can still prevent connection and are explained in the
desktop diagnostics.
