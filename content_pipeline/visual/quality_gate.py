from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Any


def assess_visual_asset_quality(image_ref: str) -> dict[str, Any]:
    path = Path(image_ref) if image_ref else None
    if path is None or not path.is_file():
        return {
            "exists": False,
            "width": None,
            "height": None,
            "file_size_bytes": None,
            "readability": "missing_file",
            "reason": "image_ref_missing_or_not_a_file",
        }

    file_size = _file_size(path)
    width, height, reason = _read_dimensions(path)
    if width is None or height is None:
        return {
            "exists": True,
            "width": None,
            "height": None,
            "file_size_bytes": file_size,
            "readability": "unknown",
            "reason": reason or "image_dimensions_unavailable",
        }
    if width < 50 or height < 50:
        readability = "too_small"
        reason = "width_or_height_below_50px"
    elif width < 180 or height < 180:
        readability = "low_resolution"
        reason = "width_or_height_below_180px"
    else:
        readability = "usable"
        reason = "image_dimensions_usable"
    return {
        "exists": True,
        "width": width,
        "height": height,
        "file_size_bytes": file_size,
        "readability": readability,
        "reason": reason,
    }


def _file_size(path: Path) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _read_dimensions(path: Path) -> tuple[int | None, int | None, str]:
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            width, height = image.size
            return int(width), int(height), "read_by_pillow"
    except Exception:
        pass
    return _read_common_image_dimensions(path)


def _read_common_image_dimensions(path: Path) -> tuple[int | None, int | None, str]:
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                width, height = struct.unpack(">II", header[16:24])
                return int(width), int(height), "read_png_header"
            if header.startswith(b"\xff\xd8"):
                handle.seek(2)
                return _read_jpeg_dimensions(handle)
    except OSError:
        return None, None, "image_file_unreadable"
    return None, None, "unsupported_image_format_without_pillow"


def _read_jpeg_dimensions(handle: Any) -> tuple[int | None, int | None, str]:
    while True:
        marker_start = handle.read(1)
        if not marker_start:
            return None, None, "jpeg_dimensions_not_found"
        if marker_start != b"\xff":
            continue
        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if not marker:
            return None, None, "jpeg_dimensions_not_found"
        marker_code = marker[0]
        if marker_code in {0xD8, 0xD9}:
            continue
        length_bytes = handle.read(2)
        if len(length_bytes) != 2:
            return None, None, "jpeg_segment_truncated"
        segment_length = struct.unpack(">H", length_bytes)[0]
        if segment_length < 2:
            return None, None, "jpeg_segment_invalid"
        if marker_code in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            data = handle.read(segment_length - 2)
            if len(data) < 5:
                return None, None, "jpeg_sof_truncated"
            height, width = struct.unpack(">HH", data[1:5])
            return int(width), int(height), "read_jpeg_header"
        handle.seek(segment_length - 2, os.SEEK_CUR)
