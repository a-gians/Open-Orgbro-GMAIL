#!/usr/bin/env python3
"""Print generated text on ORGBRO X3 using the PacketLogger-derived Q2 path.

The real Snap & Tag text job we captured uses:

    0x0a:78        speed/setup
    0x09:0c        density/setup
    0x00:<raster>  image/text raster chunks
    0x02:c800      feed

    The 0x00 payloads line up as 432 bytes per frame. Empirically the X3
    interprets those as 864 dots wide, i.e. 108 bytes per row and 4 rows per
    frame. Using 432 dots caused two adjacent rendered rows to appear side by
    side, duplicating the text horizontally.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bleak import BleakClient
from PIL import Image, ImageDraw, ImageFont

from x3_ble import NOTIFY_CHARS, WRITE_CHAR, parse_yk_frame, resolve_address, sender_id, yk_frame


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_font(font_size: int, font_path: str | None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        font_path,
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, font_size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_text_image(
    *,
    text: str,
    width_dots: int,
    height_rows: int,
    x: int | None,
    y: int | None,
    align: str,
    valign: str,
    font_size: int,
    font_path: str | None,
) -> Image.Image:
    image = Image.new("1", (width_dots, height_rows), 1)
    draw = ImageDraw.Draw(image)
    font = load_font(font_size, font_path)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    if x is None:
        if align == "left":
            x = 0
        elif align == "center":
            x = max(0, (width_dots - text_width) // 2)
        elif align == "right":
            x = max(0, width_dots - text_width)
        else:
            raise ValueError(f"unsupported align: {align}")
    if y is None:
        if valign == "top":
            y = 0
        elif valign == "middle":
            y = max(0, (height_rows - text_height) // 2)
        elif valign == "bottom":
            y = max(0, height_rows - text_height)
        else:
            raise ValueError(f"unsupported valign: {valign}")
    x -= bbox[0]
    y -= bbox[1]
    draw.text((x, y), text, fill=0, font=font)
    return image


def pack_image_msb_first(image: Image.Image) -> bytes:
    if image.mode != "1":
        image = image.convert("1")
    width, height = image.size
    width_bytes = (width + 7) // 8
    payload = bytearray(width_bytes * height)
    pixels = image.load()
    for y in range(height):
        row_offset = y * width_bytes
        for x in range(width):
            if pixels[x, y] == 0:
                payload[row_offset + (x // 8)] |= 0x80 >> (x % 8)
    return bytes(payload)


def chunk_rows(raster: bytes, width_bytes: int, rows_per_chunk: int) -> list[bytes]:
    chunk_size = width_bytes * rows_per_chunk
    return [raster[i : i + chunk_size] for i in range(0, len(raster), chunk_size)]


def chunk_bytes(data: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]


async def print_text(address: str, args: argparse.Namespace) -> dict[str, Any]:
    notifications: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []

    def on_notify(sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        event: dict[str, Any] = {
            "timestamp": now_iso(),
            "sender": sender_id(sender),
            "hex": raw.hex(),
        }
        parsed = parse_yk_frame(raw)
        if parsed is not None:
            event["yk_frame"] = parsed
        notifications.append(event)

    image = render_text_image(
        text=args.text,
        width_dots=args.width_dots,
        height_rows=args.height_rows,
        x=args.x,
        y=args.y,
        align=args.align,
        valign=args.valign,
        font_size=args.font_size,
        font_path=args.font_path,
    )
    width_bytes = (args.width_dots + 7) // 8
    raster = pack_image_msb_first(image)
    raster_chunks = chunk_rows(raster, width_bytes, args.rows_per_chunk)

    seq = args.seq_start
    frames: list[tuple[str, bytes]] = [
        ("speed_78", yk_frame(0x0A, b"\x78", seq)),
        ("density_0c", yk_frame(0x09, b"\x0c", seq + 1)),
    ]
    seq += 2
    for chunk in raster_chunks:
        frames.append((f"raster_{seq:02d}", yk_frame(0x00, chunk, seq)))
        seq += 1
    frames.append(("feed_c800", yk_frame(0x02, args.feed_steps.to_bytes(2, "little"), seq)))

    if args.raw_chunk_size:
        stream = b"".join(frame for _, frame in frames)
        write_plan = [
            (f"raw_{index:03d}", chunk)
            for index, chunk in enumerate(chunk_bytes(stream, args.raw_chunk_size), start=1)
        ]
    else:
        write_plan = frames

    async with BleakClient(address) as client:
        for char_uuid in NOTIFY_CHARS:
            try:
                await client.start_notify(char_uuid, on_notify)
            except Exception as exc:
                notifications.append({"timestamp": now_iso(), "subscribe_failed": char_uuid, "error": repr(exc)})

        await asyncio.sleep(args.initial_delay)

        for label, frame in write_plan:
            await client.write_gatt_char(WRITE_CHAR, frame, response=False)
            writes.append(
                {
                    "timestamp": now_iso(),
                    "label": label,
                    "len": len(frame),
                    "hex_prefix": frame[:32].hex(),
                }
            )
            await asyncio.sleep(args.delay)

        await asyncio.sleep(args.wait_after)

        for char_uuid in NOTIFY_CHARS:
            try:
                await client.stop_notify(char_uuid)
            except Exception:
                pass

    return {
        "timestamp": now_iso(),
        "text_length": len(args.text),
        "width_dots": args.width_dots,
        "height_rows": args.height_rows,
        "width_bytes": width_bytes,
        "rows_per_chunk": args.rows_per_chunk,
        "raw_chunk_size": args.raw_chunk_size,
        "raster_chunk_count": len(raster_chunks),
        "writes": writes,
        "notifications": notifications,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="?", default="Hello world")
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--width-dots", type=int, default=864)
    parser.add_argument("--height-rows", type=int, default=180)
    parser.add_argument("--rows-per-chunk", type=int, default=4)
    parser.add_argument("--raw-chunk-size", type=int, default=240)
    parser.add_argument("--x", type=int, default=None)
    parser.add_argument("--y", type=int, default=None)
    parser.add_argument("--align", choices=["left", "center", "right"], default="center")
    parser.add_argument("--valign", choices=["top", "middle", "bottom"], default="middle")
    parser.add_argument("--font-size", type=int, default=48)
    parser.add_argument("--font-path", default=None)
    parser.add_argument("--feed-steps", type=int, default=200)
    parser.add_argument("--seq-start", type=int, default=15)
    parser.add_argument("--initial-delay", type=float, default=0.4)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--wait-after", type=float, default=4.0)
    parser.add_argument("--preview", default=None)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.width_dots % 8:
        raise SystemExit("--width-dots must be divisible by 8")
    if not 1 <= args.feed_steps <= 0xFFFF:
        raise SystemExit("--feed-steps must be between 1 and 65535")

    if args.preview:
        preview = render_text_image(
            text=args.text,
            width_dots=args.width_dots,
            height_rows=args.height_rows,
            x=args.x,
            y=args.y,
            align=args.align,
            valign=args.valign,
            font_size=args.font_size,
            font_path=args.font_path,
        )
        out = Path(args.preview)
        out.parent.mkdir(parents=True, exist_ok=True)
        preview.save(out)
        if args.preview_only:
            print(json.dumps({"preview": str(out), "text_length": len(args.text)}, indent=2))
            return

    address = args.address
    if not address:
        address, _ = await resolve_address(args.filter, args.timeout)

    result = await print_text(address, args)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
