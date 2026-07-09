from __future__ import annotations

from pathlib import Path
from typing import Any

from content_pipeline.contracts.blocks import ResolvedImagePath


class ImagePathResolver:
    """Resolve MinerU image paths with one shared candidate order and cache."""

    def __init__(
        self,
        root: str | Path | None = None,
        markdown_image_map: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.root = Path(root) if root else None
        self.markdown_image_map = markdown_image_map or {}
        self._cache: dict[str, ResolvedImagePath] = {}

    def resolve(self, item: dict[str, Any]) -> ResolvedImagePath:
        candidates = self._candidate_values(item)
        for method, raw in candidates:
            if not isinstance(raw, str) or not raw.strip():
                continue
            result = self._resolve_candidate(raw, method)
            if result.resolved_path or result.normalized_value:
                return result
        return ResolvedImagePath(None, None, None, "unresolved", [])

    def _candidate_values(self, item: dict[str, Any]) -> list[tuple[str, Any]]:
        content = item.get("content")
        values: list[tuple[str, Any]] = [
            ("block.img_path", item.get("img_path")),
            ("block.image_path", item.get("image_path")),
            ("block.path", item.get("path")),
        ]
        if isinstance(content, dict):
            values.extend([
                ("content.img_path", content.get("img_path")),
                ("content.image_path", content.get("image_path")),
                ("content.path", content.get("path")),
            ])
            image_source = content.get("image_source")
            if isinstance(image_source, dict):
                values.append(("content.image_source.path", image_source.get("path")))
        for entry in self.markdown_image_map.values():
            values.append(("markdown_image_map.path", entry.get("path")))
        return values

    def _resolve_candidate(self, raw: str, method: str) -> ResolvedImagePath:
        original = raw.strip()
        normalized = original.lstrip("./")
        warnings: list[str] = []
        if normalized != original:
            warnings.append(f"path normalized by stripping leading ./ from {original!r}")
        cache_key = f"{method}:{normalized}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        resolved: str | None = None
        if self.root is not None:
            direct = self.root / normalized
            if direct.is_file():
                resolved = str(direct)
            else:
                filename = Path(normalized).name
                if filename:
                    for found in self.root.rglob(filename):
                        if found.is_file():
                            resolved = str(found)
                            method = f"{method}+filename_rglob"
                            break
        result = ResolvedImagePath(original, normalized, resolved, method, warnings)
        self._cache[cache_key] = result
        return result


def resolve_image_path(item: dict[str, Any], root: str | Path | None = None) -> ResolvedImagePath:
    return ImagePathResolver(root).resolve(item)


def image_path_from_block(item: dict[str, Any], markdown_image_map: dict[str, dict[str, str]] | None = None) -> str:
    return ImagePathResolver(markdown_image_map=markdown_image_map).resolve(item).normalized_value or ""


def image_path_from_block_in_root(item: dict[str, Any], root: str | Path | None = None) -> str | None:
    result = ImagePathResolver(root).resolve(item)
    return result.resolved_path


def resolve_image_file(root: str | Path, image_path: str) -> str | None:
    base = Path(root)
    clean = image_path.strip().lstrip("./")
    candidate = base / clean
    if candidate.is_file():
        return str(candidate)
    filename = Path(clean).name
    for found in base.rglob(filename):
        if found.is_file():
            return str(found)
    return None
