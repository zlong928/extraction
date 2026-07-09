from __future__ import annotations

import json
from pathlib import Path

from content_pipeline.mineru.content_block_normalizer import ContentBlockNormalizer
from content_pipeline.mineru.image_path_resolver import ImagePathResolver


def test_block_normalizer_preserves_structured_content_and_captions(tmp_path: Path) -> None:
    pages = [[
        {
            "type": "image",
            "content": {
                "image_caption": {"text": "Fig. 1 | Panel image"},
                "image_source": {"path": "./images/fig1.png"},
                "title_content": [{"content": "Nested title"}],
            },
            "bbox": [0, 0, 100, 100],
        }
    ]]
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    path = tmp_path / "content_list_v2.json"
    path.write_text(json.dumps(pages), encoding="utf-8")

    blocks = ContentBlockNormalizer(image_root=tmp_path).load(path)

    assert blocks[0].structured_content["title_content"][0]["content"] == "Nested title"
    assert blocks[0].caption_fields["image_caption"] == ["Fig. 1 | Panel image"]
    assert blocks[0].image_path and blocks[0].image_path.endswith("fig1.png")
    assert blocks[0].text_hash


def test_formula_latex_mathml_and_reference_markers_preserved() -> None:
    pages = [[
        {"type": "equation", "content": {"latex": "E=mc^2", "mathml": "<math/>"}},
        {"type": "ref_text", "content": "Prior work [1, 2-5] supports this."},
    ]]

    blocks = ContentBlockNormalizer().normalize_pages(pages)

    assert blocks[0].normalized_type == "formula"
    assert blocks[0].formula_latex == "E=mc^2"
    assert blocks[0].formula_mathml == "<math/>"
    assert blocks[1].normalized_type == "reference"
    assert blocks[1].reference_markers == ["[1, 2-5]"]


def test_mineru_v2_reference_list_and_formula_math_content() -> None:
    pages = [[
        {"type": "list", "content": {"list_type": "reference_list", "items": ["[1] Prior work."]}},
        {"type": "equation_interline", "content": {"math_content": "x = y + z"}},
    ]]

    blocks = ContentBlockNormalizer().normalize_pages(pages)

    assert blocks[0].normalized_type == "reference"
    assert blocks[0].metadata["list_type"] == "reference_list"
    assert blocks[0].metadata["mineru_block_type"] == "list"
    assert blocks[0].raw_block["type"] == "list"
    assert blocks[1].normalized_type == "formula"
    assert blocks[1].formula_latex == "x = y + z"


def test_caption_structured_and_rich_text_preserved() -> None:
    pages = [[{
        "type": "image",
        "content": {
            "image_caption": [
                {"type": "text", "content": "CO2 capture rate of"},
                {"type": "equation_inline", "content": "{ \\sf H } _ { 2 }"},
            ]
        },
    }]]

    block = ContentBlockNormalizer().normalize_pages(pages)[0]

    assert block.caption_fields["image_caption"] == ["CO2 capture rate of { \\sf H } _ { 2 }"]
    assert block.metadata["caption_structured"]["image_caption"] == [
        {"type": "text", "content": "CO2 capture rate of"},
        {"type": "equation_inline", "content": "{ \\sf H } _ { 2 }"},
    ]
    assert block.metadata["caption_rich_text"] == "<text>CO2 capture rate of</text> <equation_inline>{ \\sf H } _ { 2 }</equation_inline>"


def test_table_html_preserved() -> None:
    pages = [[{"type": "table", "content": {"table_html": "<table><tr><td>A</td></tr></table>", "table_caption": "Table 1"}}]]

    block = ContentBlockNormalizer().normalize_pages(pages)[0]

    assert block.normalized_type == "table"
    assert block.table_html == "<table><tr><td>A</td></tr></table>"
    assert block.caption_fields["table_caption"] == ["Table 1"]


def test_image_path_resolver_records_normalization_warning(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig.png").write_bytes(b"img")

    result = ImagePathResolver(tmp_path).resolve({"content": {"image_source": {"path": "./images/fig.png"}}})

    assert result.resolved_path and result.resolved_path.endswith("fig.png")
    assert result.warnings
    assert "filename_rglob" in result.resolution_method or "path" in result.resolution_method


def test_structured_content_not_flattened(tmp_path: Path) -> None:
    pages = [[{"type": "paragraph", "content": {"title": "Complex", "nested": {"deep": "value"}, "items": [1, 2, 3]}}]]
    blocks = ContentBlockNormalizer().normalize_pages(pages)

    assert blocks[0].structured_content["nested"]["deep"] == "value"
    assert blocks[0].structured_content["items"] == [1, 2, 3]
    assert "structured only" not in (blocks[0].text or "")


def test_image_path_resolver_provenance(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig.png").write_bytes(b"img")

    resolver = ImagePathResolver(tmp_path)
    result = resolver.resolve({"content": {"image_source": {"path": "./images/fig.png"}}})

    assert result.original_value == "./images/fig.png"
    assert result.normalized_value == "images/fig.png"
    assert result.resolved_path and result.resolved_path.endswith("fig.png")
    assert isinstance(result.resolution_method, str)
    assert len(result.resolution_method) > 0
