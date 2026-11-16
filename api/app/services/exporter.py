"""Document exporters — self-contained HTML and Markdown (spec §6.2, P2-08).

The HTML must be *truly* self-contained: no external CSS, no external JS, zero
`<script>` tags. Charts are therefore serialised to inline SVG by hand — the
Python side has no ECharts, and shipping a headless browser is out of scope.
``line`` / ``bar`` / ``area`` get real SVG; ``scatter`` / ``waterfall`` degrade to
a data table plus a note, which is honest rather than broken.
"""

from __future__ import annotations

import html
import re
from typing import Any

from .block_text import CITATION_RE

SVG_WIDTH = 640
SVG_HEIGHT = 260
SVG_PADDING = {"top": 28, "right": 16, "bottom": 34, "left": 52}

STANCE_LABEL = {"buy": "看多", "hold": "中性", "sell": "看空", "watch": "观察"}
SEVERITY_LABEL = {"low": "低", "medium": "中", "high": "高"}
SCENARIO_LABEL = {"bear": "悲观", "base": "中性", "bull": "乐观"}
TONE_LABEL = {"info": "说明", "warn": "注意", "insight": "洞察"}

#: `CITATION_RE` is **imported**, never re-declared (★V4-P1-3): export links `[S1]`
#: and `ask` grounds on `[S1]`, so the two must read the same regex or "点得进去"
#: and "答得出来" drift apart. `services/block_text` is the one definition.


# ── helpers ───────────────────────────────────────────────────────────────


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def render_citations(text: str, sources_by_id: dict[str, dict[str, Any]]) -> str:
    """Turn `[S1,S2]` into a plain superscript marker (no JS, no modal)."""

    def replace(match: re.Match[str]) -> str:
        ids = [item.strip() for item in match.group(1).split(",")]
        titles = "; ".join(
            f"{source_id} {sources_by_id.get(source_id, {}).get('title', '')}".strip() for source_id in ids
        )
        return f'<sup class="cite" title="{_esc(titles)}">[{_esc(match.group(1))}]</sup>'

    return CITATION_RE.sub(replace, _esc(text))


def _svg_axes(numbers: list[float]) -> tuple[float, float, int]:
    finite = [value for value in numbers if value is not None]
    if not finite:
        return 0.0, 1.0, 4
    low, high = min(finite), max(finite)
    if high == low:
        high = low + 1.0
    span = high - low
    return low - span * 0.08, high + span * 0.08, 4


def render_chart_svg(chart: dict[str, Any]) -> str:
    """Hand-rolled SVG for line / bar / area. No dependencies, no JS."""
    kind = str(chart.get("kind") or "line")
    title = str(chart.get("title") or "")
    unit = str(chart.get("unit") or "")
    series_list: list[dict[str, Any]] = chart.get("series") or []
    axis = chart.get("xAxis") or {}
    labels: list[str] = [str(label) for label in (axis.get("labels") or [])]

    if not series_list or not any(series.get("data") for series in series_list):
        return f'<p class="chart-empty">图表「{_esc(title)}」没有可用数据。</p>'

    all_numbers = [value for series in series_list for value in (series.get("data") or []) if value is not None]
    low, high, ticks = _svg_axes([float(value) for value in all_numbers])
    series_length = max(len(series.get("data") or []) for series in series_list)
    if not labels:
        labels = [str(index + 1) for index in range(series_length)]

    plot_w = SVG_WIDTH - SVG_PADDING["left"] - SVG_PADDING["right"]
    plot_h = SVG_HEIGHT - SVG_PADDING["top"] - SVG_PADDING["bottom"]
    baseline = SVG_PADDING["top"] + plot_h

    def x_at(index: int) -> float:
        if series_length <= 1:
            return SVG_PADDING["left"] + plot_w / 2
        return SVG_PADDING["left"] + (plot_w * index) / (series_length - 1)

    def y_at(value: float) -> float:
        if high == low:
            return baseline
        return baseline - plot_h * ((value - low) / (high - low))

    parts: list[str] = [
        f'<svg class="chart" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img" '
        f'aria-label="{_esc(title)}" xmlns="http://www.w3.org/2000/svg">'
    ]

    # horizontal grid + y labels
    for tick in range(ticks + 1):
        value = low + (high - low) * tick / ticks
        y = y_at(value)
        parts.append(
            f'<line x1="{SVG_PADDING["left"]}" y1="{y:.1f}" x2="{SVG_WIDTH - SVG_PADDING["right"]}" y2="{y:.1f}" '
            'stroke="rgba(255,255,255,0.08)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{SVG_PADDING["left"] - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'fill="#8a9099" font-size="10">{_fmt_num(value, 1 if abs(high - low) < 50 else 0)}</text>'
        )

    # x labels (sparse)
    step = max(1, series_length // 6)
    for index in range(0, series_length, step):
        label = labels[index] if index < len(labels) else str(index + 1)
        parts.append(
            f'<text x="{x_at(index):.1f}" y="{SVG_HEIGHT - 12}" text-anchor="middle" '
            f'fill="#8a9099" font-size="10">{_esc(label[:8])}</text>'
        )

    palette = ["#3ecf8e", "#4c9aff", "#f5a623", "#f4574d", "#a78bfa", "#22d3ee"]
    if kind == "bar":
        bar_group = plot_w / max(1, series_length)
        bar_width = max(2.0, (bar_group * 0.7) / max(1, len(series_list)))
        for series_index, series in enumerate(series_list):
            color = palette[series_index % len(palette)]
            for index, value in enumerate(series.get("data") or []):
                if value is None:
                    continue
                x = SVG_PADDING["left"] + bar_group * index + bar_group * 0.15 + bar_width * series_index
                y = y_at(float(value))
                parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{max(0.0, baseline - y):.1f}" '
                    f'fill="{color}" opacity="0.85"/>'
                )
    else:
        for series_index, series in enumerate(series_list):
            color = palette[series_index % len(palette)]
            points: list[tuple[float, float]] = []
            for index, value in enumerate(series.get("data") or []):
                if value is None:
                    continue
                points.append((x_at(index), y_at(float(value))))
            if not points:
                continue
            path = " ".join(f"{'M' if position == 0 else 'L'}{x:.1f},{y:.1f}" for position, (x, y) in enumerate(points))
            if kind == "area" and points:
                area_path = (
                    f"M{points[0][0]:.1f},{baseline:.1f} "
                    + " ".join(f"L{x:.1f},{y:.1f}" for x, y in points)
                    + f" L{points[-1][0]:.1f},{baseline:.1f} Z"
                )
                parts.append(f'<path d="{area_path}" fill="{color}" opacity="0.18"/>')
            parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
            if series_length <= 24:
                for x, y in points:
                    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{color}"/>')

    legend_x = SVG_PADDING["left"]
    for series_index, series in enumerate(series_list):
        color = palette[series_index % len(palette)]
        parts.append(f'<rect x="{legend_x}" y="8" width="10" height="10" rx="2" fill="{color}"/>')
        name = str(series.get("name") or f"序列{series_index + 1}")
        parts.append(f'<text x="{legend_x + 15}" y="17" fill="#c9ced6" font-size="11">{_esc(name)}</text>')
        legend_x += 24 + 7 * len(name) + 18

    parts.append("</svg>")
    caption = f"<figcaption>{_esc(title)}{f'（单位：{_esc(unit)}）' if unit else ''}</figcaption>"
    return f'<figure class="chart-figure">{"".join(parts)}{caption}</figure>'


def render_series_table(chart: dict[str, Any]) -> str:
    """Degraded rendering for chart kinds we cannot draw in pure Python."""
    labels = [str(label) for label in ((chart.get("xAxis") or {}).get("labels") or [])]
    series_list: list[dict[str, Any]] = chart.get("series") or []
    head = "".join(f"<th>{_esc(series.get('name') or '数据')}</th>" for series in series_list)
    rows: list[str] = []
    length = max([len(series.get("data") or []) for series in series_list] or [0])
    for index in range(length):
        label = labels[index] if index < len(labels) else str(index + 1)
        cells = "".join(
            f'<td class="num">{_fmt_num((series.get("data") or [None] * (index + 1))[index] if index < len(series.get("data") or []) else None)}</td>'
            for series in series_list
        )
        rows.append(f"<tr><th>{_esc(label)}</th>{cells}</tr>")
    note = str(chart.get("note") or "")
    return (
        f'<table class="data"><caption>{_esc(chart.get("title") or "")}'
        f"{'（导出版降级为数据表）' if not note else ''}</caption>"
        f"<thead><tr><th>项目</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        + (f'<p class="note">{_esc(note)}</p>' if note else "")
    )


# ── block renderers ───────────────────────────────────────────────────────


def render_block(block: dict[str, Any], sources_by_id: dict[str, dict[str, Any]]) -> str:
    kind = block.get("type")
    if kind == "paragraph":
        return f"<p>{render_citations(str(block.get('text') or ''), sources_by_id)}</p>"
    if kind == "heading":
        level = block.get("level") if block.get("level") in (2, 3, 4) else 2
        text = _esc(block.get("text") or "")
        return f'<h{level} id="h-{abs(hash(text)) % 100000}">{text}</h{level}>'
    if kind == "bullets":
        items = "".join(
            f"<li>{render_citations(str(item.get('text') or ''), sources_by_id)}</li>"
            for item in block.get("items") or []
        )
        return f"<ul>{items}</ul>"
    if kind == "callout":
        tone = block.get("tone") if block.get("tone") in ("info", "warn", "insight") else "info"
        return (
            f'<aside class="callout {tone}"><strong>{_esc(block.get("title"))}</strong>'
            f"<p>{render_citations(str(block.get('text') or ''), sources_by_id)}</p></aside>"
        )
    if kind == "metrics":
        cells = []
        for item in block.get("items") or []:
            delta = item.get("deltaPct")
            delta_html = ""
            if delta is not None:
                arrow = "▲" if float(delta) >= 0 else "▼"
                delta_html = f'<span class="delta">{"+" if float(delta) >= 0 else ""}{float(delta):.1f}% {arrow}</span>'
            refs = "".join(f'<sup class="cite">[{_esc(ref)}]</sup>' for ref in item.get("sourceRefs") or [])
            cells.append(
                f'<div class="metric"><span class="label">{_esc(item.get("label"))}</span>'
                f'<span class="value">{_esc(item.get("value"))}</span>{delta_html}{refs}</div>'
            )
        return f'<div class="metrics">{"".join(cells)}</div>'
    if kind == "table":
        columns = block.get("columns") or []
        head = "".join(
            f'<th class="{_esc(column.get("align") or "left")}">{_esc(column.get("label"))}</th>' for column in columns
        )
        body = []
        for row in block.get("rows") or []:
            cells = "".join(
                f'<td class="{_esc(column.get("align") or "left")}">{_esc(row.get(column.get("key"), "—"))}</td>'
                for column in columns
            )
            body.append(f"<tr>{cells}</tr>")
        caption = f"<caption>{_esc(block.get('caption'))}</caption>" if block.get("caption") else ""
        return f'<table class="data">{caption}<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
    if kind == "chart":
        chart = block.get("chart") or {}
        if chart.get("kind") in ("line", "bar", "area"):
            return render_chart_svg(chart)
        return render_series_table(chart)
    if kind == "quote":
        refs = ", ".join(f"[{ref}]" for ref in block.get("sourceRefs") or [])
        return (
            f"<blockquote><p>{render_citations(str(block.get('text') or ''), sources_by_id)}</p>"
            f"<footer>— {_esc(block.get('attribution'))} {_esc(refs)}</footer></blockquote>"
        )
    if kind == "timeline":
        items = []
        for event in block.get("events") or []:
            detail = (
                f"<p>{render_citations(str(event.get('detail') or ''), sources_by_id)}</p>"
                if event.get("detail")
                else ""
            )
            items.append(
                f"<li><time>{_esc(event.get('date'))}</time><strong>{_esc(event.get('title'))}</strong>{detail}</li>"
            )
        return f'<ol class="timeline">{"".join(items)}</ol>'
    if kind == "comparables":
        rows = []
        for peer in block.get("peers") or []:
            rows.append(
                "<tr>"
                f'<th>{_esc(peer.get("name"))}<span class="sym">{_esc(peer.get("symbol"))}</span></th>'
                f'<td class="num">{_fmt_num(peer.get("pe"), 1)}</td>'
                f'<td class="num">{_fmt_num(peer.get("pb"), 1)}</td>'
                f'<td class="num">{_fmt_num(peer.get("growthPct"), 1)}%</td>'
                f"<td>{_esc(peer.get('note') or '')}</td>"
                "</tr>"
            )
        return (
            '<table class="data"><caption>同业对比</caption><thead><tr><th>公司</th>'
            '<th class="num">PE</th><th class="num">PB</th><th class="num">增速</th><th>备注</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    if kind == "scenario":
        rows = []
        for scenario in block.get("scenarios") or []:
            name = SCENARIO_LABEL.get(str(scenario.get("name")), str(scenario.get("name")))
            probability = scenario.get("probability")
            rows.append(
                "<tr>"
                f"<th>{_esc(name)}</th>"
                f'<td class="num">{_fmt_num(scenario.get("targetPrice"), 2)}</td>'
                f'<td class="num">{f"{float(probability) * 100:.0f}%" if probability is not None else "—"}</td>'
                f"<td>{_esc(scenario.get('assumptions'))}</td>"
                "</tr>"
            )
        return (
            '<table class="data"><caption>三情景估值</caption><thead><tr><th>情景</th>'
            '<th class="num">目标价</th><th class="num">概率</th><th>核心假设</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    if kind == "divider":
        return '<hr class="divider"/>'
    return ""


# ── document renderers ────────────────────────────────────────────────────


def _document_head(doc: dict[str, Any]) -> str:
    instrument = doc.get("instrument") or {}
    rating = doc.get("rating") or {}
    stance = str(rating.get("stance") or "watch")
    return (
        f'<header class="doc-head">'
        f'<div class="sym">{_esc(instrument.get("symbol"))} · {_esc(instrument.get("market"))}</div>'
        f"<h1>{_esc(instrument.get('name'))}</h1>"
        f'<p class="headline">{_esc(doc.get("headline"))}</p>'
        f'<div class="tags"><span class="stance {stance}">{STANCE_LABEL.get(stance, stance)}</span>'
        f'<span class="tag">信心 {_esc(rating.get("conviction"))}/5</span>'
        f'<span class="tag">持有期 {_esc(rating.get("horizon"))}</span>'
        + (
            f'<span class="tag">目标价 {_fmt_num(rating.get("targetPrice"), 2)}</span>'
            if rating.get("targetPrice")
            else ""
        )
        + (
            f'<span class="tag">空间 {float(rating["upsidePct"]):+.1f}%</span>'
            if rating.get("upsidePct") is not None
            else ""
        )
        + f'</div><p class="asof">数据时点 {_esc(doc.get("asOf"))}</p></header>'
    )


def render_html(doc: dict[str, Any], *, title: str, generated_at: str) -> str:
    """Self-contained HTML: inline CSS, inline SVG, zero `<script>` tags."""
    sources_by_id = {str(source.get("id")): source for source in doc.get("sources") or []}
    sections_html: list[str] = []
    for section in doc.get("sections") or []:
        blocks = "".join(render_block(block, sources_by_id) for block in section.get("blocks") or [])
        sections_html.append(
            f'<section id="sec-{_esc(section.get("id"))}"><h2>{_esc(section.get("title"))}</h2>{blocks}</section>'
        )

    metrics = doc.get("keyMetrics") or []
    metrics_html = ""
    if metrics:
        cells = "".join(
            f'<div class="metric"><span class="label">{_esc(item.get("label"))}</span>'
            f'<span class="value">{_esc(item.get("value"))}</span></div>'
            for item in metrics
        )
        metrics_html = f'<div class="metrics key">{"".join(cells)}</div>'

    risks_html = ""
    if doc.get("risks"):
        rows = "".join(
            f"<tr><th>{_esc(risk.get('title'))}</th>"
            f'<td class="sev {_esc(risk.get("severity"))}">{SEVERITY_LABEL.get(str(risk.get("severity")), "")}</td>'
            f"<td>{_esc(risk.get('trigger'))}</td><td>{_esc(risk.get('mitigant') or '—')}</td></tr>"
            for risk in doc["risks"]
        )
        risks_html = (
            '<section id="sec-risk-list"><h2>风险清单</h2><table class="data"><thead><tr>'
            "<th>风险</th><th>等级</th><th>触发条件</th><th>应对</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></section>"
        )

    sources_html = ""
    if sources_by_id:
        items = []
        for source in doc["sources"]:
            published = source.get("publishedAt")
            published_html = f" · {_esc(published)}" if published else ""
            url = str(source.get("url") or "")
            link_html = (
                f' · <a href="{_esc(url)}" rel="noopener noreferrer" target="_blank">链接</a>'
                if url.startswith(("http://", "https://"))
                else ""
            )
            items.append(
                f'<li id="src-{_esc(source.get("id"))}"><strong>{_esc(source.get("id"))}</strong> '
                f"{_esc(source.get('title'))} · {_esc(source.get('publisher'))}"
                f"{published_html}{link_html}</li>"
            )
        sources_html = f'<section id="sec-sources"><h2>来源</h2><ol class="sources">{"".join(items)}</ol></section>'

    provisional_banner = (
        '<p class="banner">本次生成未通过结构校验，以下为原始产出摘要，建议重新生成。</p>'
        if doc.get("provisional")
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
<style>
:root{{--bg:#0b0c0e;--s1:#121316;--s2:#17181c;--bd:rgba(255,255,255,.09);--tx:#edeef0;--tx2:#a0a5ad;--tx3:#6c7178;--ac:#3ecf8e;--warn:#f5a623;--danger:#f4574d;--info:#4c9aff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.8 "Inter","SF Pro Text",-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:860px;margin:0 auto;padding:48px 24px 96px}}
.doc-head{{border-bottom:1px solid var(--bd);padding-bottom:24px;margin-bottom:32px}}
.doc-head .sym{{font:600 12px/1.4 "JetBrains Mono",Consolas,monospace;letter-spacing:.08em;color:var(--ac);text-transform:uppercase}}
.doc-head h1{{font-size:30px;line-height:1.25;margin:8px 0 6px;letter-spacing:-.01em}}
.headline{{color:var(--tx2);font-size:16px;margin:0 0 16px}}
.tags{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.stance{{font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;border:1px solid var(--bd)}}
.stance.buy{{color:var(--ac);border-color:rgba(62,207,142,.4);background:rgba(62,207,142,.12)}}
.stance.sell{{color:var(--danger);border-color:rgba(244,87,77,.4);background:rgba(244,87,77,.12)}}
.stance.hold,.stance.watch{{color:var(--tx2)}}
.tag{{font-size:12px;color:var(--tx2);padding:3px 9px;border:1px solid var(--bd);border-radius:6px;font-variant-numeric:tabular-nums}}
.asof{{font-size:12px;color:var(--tx3);margin:12px 0 0}}
section{{margin:0 0 40px;scroll-margin-top:24px}}
h2{{font-size:20px;margin:0 0 16px;padding-bottom:8px;border-bottom:1px solid var(--bd)}}
h3{{font-size:16px;margin:24px 0 8px;color:var(--tx)}}
h4{{font-size:14px;margin:20px 0 8px;color:var(--tx2)}}
p{{margin:0 0 14px;color:#dcdee1}}
sup.cite{{color:var(--ac);font-size:11px;font-weight:600;cursor:help}}
ul,ol{{margin:0 0 16px;padding-left:22px}}
li{{margin:0 0 6px;color:#dcdee1}}
.callout{{border-left:3px solid var(--info);background:var(--s1);padding:14px 16px;border-radius:0 8px 8px 0;margin:0 0 18px}}
.callout.warn{{border-color:var(--warn);background:rgba(245,166,35,.08)}}
.callout.insight{{border-color:var(--ac);background:rgba(62,207,142,.08)}}
.callout strong{{display:block;font-size:13px;margin-bottom:4px;letter-spacing:.01em}}
.callout p{{margin:0;font-size:14px;color:var(--tx2)}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--bd);border:1px solid var(--bd);border-radius:10px;overflow:hidden;margin:0 0 20px}}
.metric{{background:var(--s1);padding:14px 16px;display:flex;flex-direction:column;gap:4px}}
.metric .label{{font-size:12px;color:var(--tx2)}}
.metric .value{{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}}
.metric .delta{{font-size:11px;color:var(--tx3);font-variant-numeric:tabular-nums}}
table.data{{width:100%;border-collapse:collapse;margin:0 0 20px;font-size:13px;border:1px solid var(--bd);border-radius:10px;overflow:hidden}}
table.data caption{{caption-side:top;text-align:left;color:var(--tx3);font-size:12px;padding:0 0 8px}}
table.data th,table.data td{{padding:9px 12px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}}
table.data thead th{{background:var(--s2);font-size:12px;color:var(--tx2);font-weight:500}}
table.data tbody tr:last-child th,table.data tbody tr:last-child td{{border-bottom:none}}
table.data .num{{text-align:right;font-variant-numeric:tabular-nums}}
table.data .center{{text-align:center}}
table.data .sym{{display:block;color:var(--tx3);font:400 11px "JetBrains Mono",Consolas,monospace}}
td.sev.high{{color:var(--danger)}} td.sev.medium{{color:var(--warn)}} td.sev.low{{color:var(--tx2)}}
blockquote{{margin:0 0 20px;padding:14px 18px;background:var(--s1);border-left:3px solid var(--ac);border-radius:0 8px 8px 0}}
blockquote p{{margin:0 0 8px;font-style:italic;color:var(--tx)}}
blockquote footer{{font-size:12px;color:var(--tx3)}}
ol.timeline{{list-style:none;padding-left:0;border-left:1px solid var(--bd);margin-left:6px}}
ol.timeline li{{position:relative;padding-left:20px;margin-bottom:16px}}
ol.timeline li:before{{content:"";position:absolute;left:-4px;top:8px;width:7px;height:7px;border-radius:50%;background:var(--ac)}}
ol.timeline time{{display:block;font:600 11px "JetBrains Mono",Consolas,monospace;color:var(--ac);letter-spacing:.04em}}
.chart-figure{{margin:0 0 20px;background:var(--s1);border:1px solid var(--bd);border-radius:10px;padding:12px}}
.chart{{width:100%;height:auto;display:block}}
figcaption{{font-size:12px;color:var(--tx3);padding-top:8px}}
.chart-empty,.note{{font-size:12px;color:var(--tx3)}}
hr.divider{{border:none;border-top:1px solid var(--bd);margin:28px 0}}
ol.sources{{font-size:13px;color:var(--tx2)}}
ol.sources a{{color:var(--info);text-decoration:none}}
ol.sources a:hover{{text-decoration:underline}}
.banner{{background:rgba(245,166,35,.12);border:1px solid rgba(245,166,35,.4);color:var(--warn);padding:12px 16px;border-radius:8px;font-size:13px}}
footer.doc-foot{{margin-top:48px;padding-top:20px;border-top:1px solid var(--bd);font-size:12px;color:var(--tx3)}}
@media print{{:root{{--bg:#fff;--s1:#fafafa;--s2:#f4f5f6;--tx:#17181c;--tx2:#5a6068;--tx3:#6c7178;--bd:rgba(0,0,0,.12)}}body{{background:#fff}}p,li{{color:#17181c}}}}
</style>
</head>
<body>
<div class="wrap">
{provisional_banner}
{_document_head(doc)}
<p class="summary">{_esc(doc.get("summary"))}</p>
{metrics_html}
{"".join(sections_html)}
{risks_html}
{sources_html}
<footer class="doc-foot"><p>{_esc(doc.get("disclaimer"))}</p><p>导出于 {_esc(generated_at)} · Aragon Research Terminal</p></footer>
</div>
</body>
</html>
"""


def render_markdown(doc: dict[str, Any], *, title: str, generated_at: str) -> str:
    """Markdown export — keeps section structure and `[S1]` numbering intact."""
    instrument = doc.get("instrument") or {}
    rating = doc.get("rating") or {}
    sources_by_id = {str(source.get("id")): source for source in doc.get("sources") or []}
    lines: list[str] = [
        f"# {instrument.get('name', '')}（{instrument.get('symbol', '')}）投研报告",
        "",
        f"> {doc.get('headline', '')}",
        "",
        f"- 立场：{STANCE_LABEL.get(str(rating.get('stance')), rating.get('stance'))}"
        f"（信心 {rating.get('conviction')}/5，持有期 {rating.get('horizon')}）",
    ]
    if rating.get("targetPrice"):
        lines.append(f"- 目标价：{_fmt_num(rating.get('targetPrice'), 2)}")
    if rating.get("upsidePct") is not None:
        lines.append(f"- 上行空间：{float(rating['upsidePct']):+.1f}%")
    lines += [f"- 数据时点：{doc.get('asOf', '')}", "", doc.get("summary", ""), ""]

    if doc.get("provisional"):
        lines += ["> ⚠ 本次生成未通过结构校验，以下为原始产出摘要，建议重新生成。", ""]

    metrics = doc.get("keyMetrics") or []
    if metrics:
        lines.append("## 关键指标")
        lines.append("")
        lines.append("| 指标 | 数值 | 变化 |")
        lines.append("| --- | --- | --- |")
        for item in metrics:
            delta = item.get("deltaPct")
            lines.append(
                f"| {item.get('label', '')} | {item.get('value', '')} | "
                f"{f'{float(delta):+.1f}%' if delta is not None else '—'} |"
            )
        lines.append("")

    def block_md(block: dict[str, Any]) -> list[str]:
        kind = block.get("type")
        if kind == "paragraph":
            return [str(block.get("text") or ""), ""]
        if kind == "heading":
            level = int(block.get("level") or 2) - 1
            return [f"{'#' * max(2, level)} {block.get('text', '')}", ""]
        if kind == "bullets":
            return [f"- {item.get('text', '')}" for item in block.get("items") or []] + [""]
        if kind == "callout":
            return [f"> **{block.get('title', '')}**", f"> {block.get('text', '')}", ""]
        if kind == "metrics":
            out = ["| 指标 | 数值 |", "| --- | --- |"]
            for item in block.get("items") or []:
                out.append(f"| {item.get('label', '')} | {item.get('value', '')} |")
            return out + [""]
        if kind == "table":
            columns = block.get("columns") or []
            out = ["| " + " | ".join(str(column.get("label", "")) for column in columns) + " |"]
            out.append("| " + " | ".join("---" for _ in columns) + " |")
            for row in block.get("rows") or []:
                out.append("| " + " | ".join(str(row.get(column.get("key"), "")) for column in columns) + " |")
            if block.get("caption"):
                out.append("")
                out.append(f"*{block['caption']}*")
            return out + [""]
        if kind == "chart":
            chart = block.get("chart") or {}
            out = [f"**{chart.get('title', '')}**", ""]
            out.append(
                "| 项目 | " + " | ".join(str(series.get("name", "")) for series in chart.get("series") or []) + " |"
            )
            out.append("| --- | " + " | ".join("---" for _ in chart.get("series") or []) + " |")
            labels = [str(label) for label in ((chart.get("xAxis") or {}).get("labels") or [])]
            length = max([len(series.get("data") or []) for series in chart.get("series") or []] or [0])
            for index in range(length):
                label = labels[index] if index < len(labels) else str(index + 1)
                values = []
                for series in chart.get("series") or []:
                    data = series.get("data") or []
                    values.append(_fmt_num(data[index] if index < len(data) else None))
                out.append(f"| {label} | " + " | ".join(values) + " |")
            if chart.get("note"):
                out.append("")
                out.append(f"*{chart['note']}*")
            return out + [""]
        if kind == "quote":
            return [f"> {block.get('text', '')}", f"> — {block.get('attribution', '')}", ""]
        if kind == "timeline":
            return [
                f"- `{event.get('date', '')}` **{event.get('title', '')}** — {event.get('detail', '')}"
                for event in block.get("events") or []
            ] + [""]
        if kind == "comparables":
            out = ["| 公司 | 代码 | PE | PB | 增速 |", "| --- | --- | --- | --- | --- |"]
            for peer in block.get("peers") or []:
                out.append(
                    f"| {peer.get('name', '')} | {peer.get('symbol', '')} | {_fmt_num(peer.get('pe'), 1)} | "
                    f"{_fmt_num(peer.get('pb'), 1)} | {_fmt_num(peer.get('growthPct'), 1)}% |"
                )
            return out + [""]
        if kind == "scenario":
            out = ["| 情景 | 目标价 | 概率 | 假设 |", "| --- | --- | --- | --- |"]
            for scenario in block.get("scenarios") or []:
                probability = scenario.get("probability")
                out.append(
                    f"| {SCENARIO_LABEL.get(str(scenario.get('name')), scenario.get('name'))} | "
                    f"{_fmt_num(scenario.get('targetPrice'))} | "
                    f"{f'{float(probability) * 100:.0f}%' if probability is not None else '—'} | "
                    f"{scenario.get('assumptions', '')} |"
                )
            return out + [""]
        if kind == "divider":
            return ["---", ""]
        return []

    for section in doc.get("sections") or []:
        lines.append(f"## {section.get('title', '')}")
        lines.append("")
        lines.append(f'<a id="sec-{section.get("id")}"></a>')
        for block in section.get("blocks") or []:
            lines.extend(block_md(block))

    risks = doc.get("risks") or []
    if risks:
        lines += ["## 风险清单", "", "| 风险 | 等级 | 触发条件 | 应对 |", "| --- | --- | --- | --- |"]
        for risk in risks:
            lines.append(
                f"| {risk.get('title', '')} | {SEVERITY_LABEL.get(str(risk.get('severity')), '')} | "
                f"{risk.get('trigger', '')} | {risk.get('mitigant') or '—'} |"
            )
        lines.append("")

    if sources_by_id:
        lines += ["## 来源", ""]
        for source in doc.get("sources") or []:
            url = str(source.get("url") or "")
            link = f"[链接]({url})" if url.startswith(("http://", "https://")) else ""
            published = source.get("publishedAt")
            published_part = f" · {published}" if published else ""
            lines.append(
                f"- **{source.get('id')}** {source.get('title', '')} · {source.get('publisher', '')}"
                f"{published_part} {link}".rstrip()
            )
        lines.append("")

    lines += ["---", "", f"*{doc.get('disclaimer', '')}*", "", f"导出于 {generated_at} · Aragon Research Terminal", ""]
    return "\n".join(lines)


def export_filename(symbol: str, generated_at: str, version: int, extension: str) -> str:
    """`600519.SH-2026-02-10-v3.html` (§6.2)."""
    day = (generated_at or "")[:10] or "unknown"
    safe_symbol = re.sub(r"[^A-Za-z0-9.\-]", "-", symbol or "report")
    return f"{safe_symbol}-{day}-v{version}.{extension}"
