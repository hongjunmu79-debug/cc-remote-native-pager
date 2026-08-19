"""Small bounded framing protocol used by the local Claude PTY broker."""

from __future__ import annotations

from enum import IntEnum
import json
import struct
from typing import Any


# One MiB is ample for pasted terminal input while still bounding every
# allocation made from an untrusted frame header.  PTY output is emitted in
# much smaller chunks by session.py.
MAX_FRAME_BYTES = 1024 * 1024
MAX_CONTROL_BYTES = 512 * 1024
BROKER_PROTOCOL_VERSION = 2
HEADER = struct.Struct("!BI")


class FrameType(IntEnum):
    COMMAND = 1
    RESPONSE = 2
    INPUT = 3
    OUTPUT = 4
    RESIZE = 5
    EXIT = 6
    ERROR = 7
    DETACH = 8


class ProtocolError(ValueError):
    """The peer sent a malformed or over-sized frame."""


async def read_frame(reader: Any, *, max_size: int = MAX_FRAME_BYTES) -> tuple[FrameType, bytes]:
    """Read one complete frame without trusting its advertised allocation size."""
    if max_size < 0 or max_size > MAX_FRAME_BYTES:
        raise ValueError("invalid frame size limit")
    header = await reader.readexactly(HEADER.size)
    raw_type, length = HEADER.unpack(header)
    try:
        frame_type = FrameType(raw_type)
    except ValueError as exc:
        raise ProtocolError(f"unknown frame type: {raw_type}") from exc
    if length > max_size:
        raise ProtocolError(f"frame exceeds {max_size} byte limit")
    return frame_type, await reader.readexactly(length)


async def write_frame(
    writer: Any,
    frame_type: FrameType,
    payload: bytes = b"",
    *,
    max_size: int = MAX_FRAME_BYTES,
) -> None:
    """Write one frame and apply the same bound on locally-produced data."""
    if not isinstance(payload, bytes):
        raise TypeError("frame payload must be bytes")
    if len(payload) > max_size or len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds {min(max_size, MAX_FRAME_BYTES)} byte limit")
    writer.write(HEADER.pack(int(frame_type), len(payload)) + payload)
    await writer.drain()


def encode_json(value: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProtocolError("control payload is not JSON serializable") from exc
    if len(payload) > MAX_CONTROL_BYTES:
        raise ProtocolError("control payload is too large")
    return payload


def decode_json(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_CONTROL_BYTES:
        raise ProtocolError("control payload is too large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON control payload") from exc
    if not isinstance(value, dict):
        raise ProtocolError("control payload must be a JSON object")
    return value


async def write_json(writer: Any, frame_type: FrameType, value: dict[str, Any]) -> None:
    await write_frame(
        writer, frame_type, encode_json(value), max_size=MAX_CONTROL_BYTES,
    )
