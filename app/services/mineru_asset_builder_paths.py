from __future__ import annotations

from pathlib import Path
from typing import Any

from content_pipeline.mineru.image_path_resolver import image_path_from_block, resolve_image_file


def resolve_image(root: Path, image_path: str) -> Path | None:
    resolved = resolve_image_file(root, image_path)
    return Path(resolved) if resolved else None


def build_context_roots_for_image(image_path: str | Path) -> list[Path]:
    roots: list[Path] = []

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved not in roots:
            roots.append(resolved)

    image = Path(image_path).resolve()
    add(image.parent)
    if image.parent.name == "images":
        add(image.parent.parent)
        add(image.parent.parent.parent)
    for parent in image.parents:
        add(parent)
    return roots


def find_mineru_file(image_path: str | Path, patterns: tuple[str, ...]) -> Path | None:
    seen: set[Path] = set()
    for root in build_context_roots_for_image(image_path):
        if not root.is_dir():
            continue
        for pattern in patterns:
            for candidate in root.glob(pattern):
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if candidate.is_file():
                    return candidate
    return None


def find_extract_dir(image_path: str | Path) -> Path | None:
    image = Path(image_path)
    for root in build_context_roots_for_image(image):
        if not root.is_dir():
            continue
        for candidate in (root / "extracted", root / "structured"):
            if candidate.is_dir():
                return candidate
        if root.name == "images":
            return root.parent
        for sub in ("extracted", "images"):
            child = root / sub
            if child.is_dir():
                return child
    if image.parent.name == "images":
        return image.parent.parent
    return None


def context_artifacts_for_image(image_path: str | Path) -> dict[str, Path | None]:
    artifacts = {
        "markdown_file": find_mineru_file(image_path, ("full.md", "*.md")),
        "content_list_file": find_mineru_file(
            image_path,
            ("*content_list_v2.json", "content_list_v2.json", "*content_list.json", "content_list.json"),
        ),
        "layout_file": find_mineru_file(image_path, ("*layout.json", "layout.json")),
        "extract_dir": find_extract_dir(image_path),
    }
    fallback = _structured_batch_artifacts(image_path)
    if fallback:
        for key in ("markdown_file", "content_list_file", "layout_file"):
            if artifacts.get(key) is None and fallback.get(key) is not None:
                artifacts[key] = fallback[key]
        if (
            fallback.get("extract_dir") is not None
            and (artifacts.get("extract_dir") is None or Path(str(artifacts["extract_dir"])).name == "structured")
        ):
            artifacts["extract_dir"] = fallback["extract_dir"]
    return artifacts


def _structured_batch_artifacts(image_path: str | Path) -> dict[str, Path | None]:
    image = Path(image_path).resolve()
    if image.parent.name != "images" or image.parent.parent.name != "structured":
        return {}
    paper_root = image.parent.parent.parent
    if not paper_root.is_dir():
        return {}
    for extracted in sorted(paper_root.glob("*/extracted")):
        if not extracted.is_dir():
            continue
        original = extracted / "images" / image.name
        if not original.is_file():
            continue
        return {
            "markdown_file": (extracted / "full.md") if (extracted / "full.md").is_file() else None,
            "content_list_file": next((
                p
                for pattern in (
                    "*content_list_v2.json",
                    "content_list_v2.json",
                    "*content_list.json",
                    "content_list.json",
                )
                for p in extracted.glob(pattern)
                if p.is_file()
            ), None),
            "layout_file": next((
                p
                for pattern in ("*layout.json", "layout.json")
                for p in extracted.glob(pattern)
                if p.is_file()
            ), None),
            "extract_dir": extracted,
        }
    return {}


def markdown_image_refs(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    refs: list[dict[str, Any]] = []
    current_section = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            current_section = stripped.lstrip("#").strip()
        for match in MARKDOWN_IMAGE_RE.finditer(line):
            raw_path = match.group(2).strip().lstrip("./")
            filename = Path(raw_path).name
            path_clean = raw_path.split('"')[0].split("'")[0].strip()
            refs.append({
                "path": path_clean,
                "filename": filename,
                "section": current_section,
                "line_index": index,
            })
    return refs


def markdown_image_paths(markdown: str, image_dir: str | Path) -> list[Path]:
    root = Path(image_dir)
    paths: list[Path] = []
    seen: set[str] = set()
    for ref in markdown_image_refs(markdown):
        filename = Path(str(ref.get("path") or "")).name
        if not filename or filename in seen:
            continue
        img_path = root / filename
        if not img_path.is_file():
            for found in root.rglob(filename):
                if found.is_file():
                    img_path = found
                    break
        if img_path.is_file():
            seen.add(filename)
            paths.append(img_path)
    return paths


def content_list_image_paths(content_list: list[dict[str, Any]], image_dir: str | Path) -> list[Path]:
    root = Path(image_dir)
    if not root.is_dir() or not isinstance(content_list, list):
        return []
    image_blocks = [
        item for item in content_list
        if isinstance(item, dict) and item.get("type") in ("image", "chart")
    ]
    seen: set[str] = set()
    paths: list[Path] = []
    for item in image_blocks:
        raw_path = image_path_from_block(item)
        if not raw_path:
            continue
        resolved = resolve_image(root, raw_path)
        if resolved is None:
            continue
        if resolved.name in seen:
            continue
        seen.add(resolved.name)
        paths.append(resolved)
    return paths
