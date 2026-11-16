"""Exports: self-contained HTML, faithful Markdown (AC-13, ★P2-08)."""

from __future__ import annotations

import re

from app.services.exporter import (
    export_filename,
    render_chart_svg,
    render_html,
    render_markdown,
    render_series_table,
)

TITLE = "贵州茅台 投研报告"


def test_html_has_zero_script_tags(sample_report: dict) -> None:
    """An exported file must be inert: no JS at all (§11.3)."""
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "<script" not in html.lower()


def test_html_has_no_external_css_or_js(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert re.search(r'href="https?://[^"]*\.css', html) is None
    assert re.search(r'src="https?://[^"]*\.js', html) is None
    assert "@import" not in html


def test_html_inlines_its_stylesheet(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "<style>" in html
    assert "3ecf8e" in html


def test_html_renders_charts_as_inline_svg(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "<svg" in html
    assert 'xmlns="http://www.w3.org/2000/svg"' in html


def test_html_escapes_user_visible_text() -> None:
    doc = {
        "schema": "aragon.research.report/v1",
        "instrument": {"symbol": "600519.SH", "name": "<script>alert(1)</script>", "market": "CN", "currency": "CNY"},
        "asOf": "2026-02-10T01:00:00Z",
        "headline": "标题 <b>加粗</b>",
        "rating": {"stance": "buy", "conviction": 3, "horizon": "6-12m"},
        "summary": "摘要内容足够长以通过校验。",
        "keyMetrics": [],
        "sections": [
            {
                "id": "thesis",
                "title": "核心逻辑",
                "blocks": [{"type": "paragraph", "text": "<img src=x onerror=alert(1)>"}],
            }
        ],
        "risks": [],
        "sources": [],
        "disclaimer": "仅供研究参考，不构成投资建议。",
    }
    html = render_html(doc, title="x", generated_at="2026-02-10T01:00:00Z")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # The payload survives as *text*, which is safe. What must not exist is an
    # element or an attribute created from it.
    assert "<img" not in html
    assert "<b>加粗</b>" not in html


def test_html_marks_a_provisional_report(sample_report: dict) -> None:
    doc = dict(sample_report)
    doc["provisional"] = True
    html = render_html(doc, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "本次生成未通过结构校验" in html


def test_html_includes_the_disclaimer(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert sample_report["disclaimer"] in html


def test_html_lists_every_source(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    for source in sample_report["sources"]:
        assert f'id="src-{source["id"]}"' in html


def test_svg_line_chart_has_a_path_and_labels() -> None:
    chart = {
        "kind": "line",
        "title": "收盘价",
        "xAxis": {"type": "category", "labels": ["a", "b", "c"]},
        "series": [{"name": "收盘", "data": [1.0, 3.0, 2.0]}],
        "unit": "CNY",
    }
    svg = render_chart_svg(chart)
    assert svg.count("<path") >= 1
    assert "收盘价" in svg
    assert "CNY" in svg


def test_svg_bar_chart_uses_rects() -> None:
    chart = {
        "kind": "bar",
        "title": "同业 PE",
        "xAxis": {"type": "category", "labels": ["甲", "乙"]},
        "series": [{"name": "PE", "data": [20.0, 30.0]}],
    }
    svg = render_chart_svg(chart)
    assert svg.count("<rect") >= 2


def test_svg_area_chart_adds_a_filled_region() -> None:
    chart = {
        "kind": "area",
        "title": "走势",
        "xAxis": {"type": "category", "labels": ["a", "b", "c"]},
        "series": [{"name": "收盘", "data": [1.0, 2.0, 3.0]}],
    }
    svg = render_chart_svg(chart)
    assert 'opacity="0.18"' in svg


def test_unsupported_chart_kind_degrades_to_a_table() -> None:
    """★P2-08: no ECharts in Python, so scatter/waterfall become data tables."""
    chart = {
        "kind": "waterfall",
        "title": "归因",
        "xAxis": {"type": "category", "labels": ["a", "b"]},
        "series": [{"name": "变化", "data": [1.0, -2.0]}],
    }
    table = render_series_table(chart)
    assert "<table" in table
    assert "导出版降级为数据表" in table


def test_chart_with_no_data_is_an_empty_state_not_a_crash() -> None:
    svg = render_chart_svg({"kind": "line", "title": "空图", "xAxis": {"type": "category"}, "series": []})
    assert "没有可用数据" in svg


def test_markdown_keeps_section_structure(sample_report: dict) -> None:
    markdown = render_markdown(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    for section in sample_report["sections"]:
        assert f"## {section['title']}" in markdown


def test_markdown_keeps_citation_numbers(sample_report: dict) -> None:
    markdown = render_markdown(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "[S1]" in markdown
    assert "[S3]" in markdown


def test_markdown_renders_tables_and_scenarios(sample_report: dict) -> None:
    markdown = render_markdown(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "| 期间 |" in markdown
    assert "| 情景 | 目标价 | 概率 | 假设 |" in markdown


def test_markdown_has_no_raw_html_wrappers(sample_report: dict) -> None:
    markdown = render_markdown(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "<div" not in markdown
    assert "<script" not in markdown


def test_markdown_includes_every_source(sample_report: dict) -> None:
    markdown = render_markdown(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    for source in sample_report["sources"]:
        assert source["title"] in markdown


def test_export_filename_matches_the_documented_pattern() -> None:
    assert export_filename("600519.SH", "2026-02-10T01:00:00Z", 3, "html") == "600519.SH-2026-02-10-v3.html"
    assert export_filename("AAPL", "2026-02-10T01:00:00Z", 1, "md") == "AAPL-2026-02-10-v1.md"


def test_export_filename_sanitises_the_symbol() -> None:
    assert "/" not in export_filename("A/B", "2026-02-10T00:00:00Z", 1, "html")


def test_html_is_standalone_html5(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert '<html lang="zh-CN">' in html
    assert 'charset="utf-8"' in html


def test_html_contains_a_print_stylesheet(sample_report: dict) -> None:
    html = render_html(sample_report, title=TITLE, generated_at="2026-02-10T01:00:00Z")
    assert "@media print" in html
