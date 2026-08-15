# Native Pager bridge protocol v1

Every web-to-native frame is a UTF-8 JSON object with `bridgeVersion: 1`,
`type`, `bridgeInstanceId`, and `emittedAt`. The instance ID changes on each
page load so native sequence validation can safely reset after a WebView
reload. Frames use the origin-restricted WebMessage channel.
Native-to-web commands are delivered to one private page function using a
JSON-quoted argument.

## Web to native

### `snapshot`

Contains connection state, focused task ID, and a bounded list of task
projections. A task contains display metadata, lifecycle/activity, a safe
status line, completion revision, subagent summaries, and allowed actions.
It never contains model credentials, cookies, raw tool input/output, file
contents, or full conversation history.

### `heartbeat`

Confirms that the bridge page is alive when no task data changed.

### `commandAck`

Confirms whether the existing web client accepted a native command. It does not
claim that the remote engine finished the action. The web side keeps a bounded
command-result cache so a duplicate command ID is acknowledged without running
the action twice.

## Native to web

### `command`

Allowed action kinds:

- `focusTask`
- `interruptTask`
- `answerQuestion`
- `setPinned`
- `refreshSessions`

The web client rejects unknown actions, malformed IDs, oversized answers,
commands for a non-focused task where focus is required, and unavailable
capabilities.

## Limits

| Item | Limit |
|---|---:|
| Web-to-native frame | 256 KiB |
| Native command frame | 16 KiB |
| Tasks | 64 |
| Subagents per task | 16 |
| Task ID | 256 characters |
| Title | 160 characters |
| Latest step | 240 characters |
| Answer | 8 KiB |
| Cached command results | 128 |

Changing semantics or required fields increments `bridgeVersion`. Additive
optional fields may remain on the same version when old clients safely ignore
them.
