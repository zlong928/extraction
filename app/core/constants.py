from __future__ import annotations

import re


MARKDOWN_IMAGE_PATTERN = r"!\[([^\]]*)\]\(([^)]+)\)"
MARKDOWN_IMAGE_RE = re.compile(MARKDOWN_IMAGE_PATTERN)


def compact_text(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return " ".join(text.split())
