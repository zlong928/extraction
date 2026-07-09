from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedElement:
    element_type: str
    text: str
    page_number: int | None = None
    extractor: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    bbox: tuple[float, float, float, float] | None = None


@dataclass(slots=True)
class ParsedPage:
    page_number: int
    profile: Any = None
    elements: list[ParsedElement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument:
    pages: list[ParsedPage]
    source_type: str
    parser_version: str = ""
    parser_engine: str = ""
    pymupdf_available: bool = True
    table_extraction_enabled: bool = False
    table_extraction_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def text_pages(self) -> list[str]:
        return ["\n".join(element.text for element in page.elements if element.text) for page in self.pages]
