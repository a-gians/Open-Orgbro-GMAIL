"""Shared ORGBRO X3 BLE protocol helpers."""

from __future__ import annotations

from typing import Any

from bleak import BleakScanner


WRITE_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARS = [
    "0000ff01-0000-1000-8000-00805f9b34fb",
    "0000ff03-0000-1000-8000-00805f9b34fb",
]


def yk_frame(command: int, payload: bytes = b"", seq: int = 1) -> bytes:
    length = len(payload)
    return bytes(
        [
            0x64,
            command & 0xFF,
            seq & 0x3F,
            length & 0xFF,
            (length >> 8) & 0xFF,
        ]
    ) + payload + b"\x00\x00\x00\x00\x9b"


def parse_yk_frame(data: bytes) -> dict[str, Any] | None:
    if len(data) < 10 or data[0] != 0x64 or data[-1] != 0x9B:
        return None
    length = data[3] | (data[4] << 8)
    expected = 10 + length
    payload = data[5 : 5 + length]
    return {
        "command": data[1],
        "seq": data[2],
        "length": length,
        "payload_hex": payload.hex(),
        "expected_total_length": expected,
        "length_matches": expected == len(data),
    }


def sender_id(sender: Any) -> str:
    return getattr(sender, "uuid", None) or str(sender)


def _match_device(name: str | None, address: str, needle: str) -> bool:
    haystack = f"{name or ''} {address}".lower()
    return needle.lower() in haystack


async def resolve_address(filter_text: str, timeout: float) -> tuple[str, str | None]:
    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if _match_device(device.name, device.address, filter_text):
            return device.address, device.name
    seen = [f"{device.name or '<no name>'} {device.address}" for device in devices]
    raise SystemExit(
        f"No BLE device matching {filter_text!r} found.\n"
        "Seen devices:\n- " + "\n- ".join(seen)
    )
