"""S3 · 逐字段收敛（AC-117 / AC-118 / C-37 / C-48）。

这一组守的是 E-93 那条真实事故：**601 字的 summary（schema 上限 600）让一份 99% 合格的
报告整份消失**，而"抢救阶梯"的入口要求 `doc is None`，所以 `salvage_doc` 在这条路上
根本不会被调用；即便调用了也没用——它内部同样先 `jsonschema.validate`。

三条判据，一条比一条难：

1. **AC-117**：601 字 summary → `errors == []`、`quality.clamp == {count:1, fields:["summary"]}`、
   `status == "degraded"`，并且**真的能落库**（`salvage_doc` 那条路也能救）。
2. **AC-118**：用例**自己遍历 schema** 收集每一个 `maxLength/maxItems/maxProperties`
   节点（不抄"54"这个数），逐个越界 1 个单位，要求每一个都能收敛、`errors == []`、
   收敛后长度 ≤ 上限、以 `…` 结尾、**且仍能通过 `jsonschema.validate`**。
3. **C-37**：`block.text` 的上限**按块型分派**（paragraph 4000 / heading **120** /
   callout 1200 / quote 800）——"一刀切 4000"会让一个 400 字的 heading 仍然整份判死。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema

from app.services import report_validator as rv

SECTIONS = ("thesis", "fundamentals", "valuation", "risks", "catalysts")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: 每种块型的最小合法形状（夹具必须**合法**，否则测的是夹具不是 clamp）。
BLOCK_TEMPLATE: dict[str, dict[str, Any]] = {
    "paragraph": {"type": "paragraph", "text": "正文"},
    "heading": {"type": "heading", "level": 2, "text": "标题"},
    "bullets": {"type": "bullets", "items": [{"text": "条目"}]},
    "callout": {"type": "callout", "tone": "info", "title": "提示", "text": "正文"},
    "metrics": {"type": "metrics", "items": [{"label": "指标", "value": "1.0"}]},
    "table": {
        "type": "table",
        "columns": [{"key": "k", "label": "列"}],
        "rows": [{"k": "1"}],
    },
    "chart": {
        "type": "chart",
        "chart": {
            "kind": "line",
            "title": "图",
            "xAxis": {"type": "category"},
            "series": [{"name": "s", "data": [1.0]}],
        },
    },
    "quote": {"type": "quote", "text": "引文", "attribution": "来源", "sourceRefs": ["S1"]},
    "timeline": {"type": "timeline", "events": [{"date": "2026-01-01", "title": "事件"}]},
    "comparables": {"type": "comparables", "peers": [{"name": "同业", "symbol": "000858.SZ"}]},
    "scenario": {
        "type": "scenario",
        "scenarios": [{"name": "base", "targetPrice": 1700.0, "probability": 0.5, "assumptions": "假设"}],
    },
    "divider": {"type": "divider"},
}


# ── 夹具 ────────────────────────────────────────────────────────────────────


def _minimal_doc(snapshot: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """一份**合法**的报告文档（11 个必填键齐备、五章齐备、一章一块）。"""
    doc: dict[str, Any] = {
        "schema": "aragon.research.report/v1",
        "instrument": {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "market": "CN",
            "currency": "CNY",
        },
        "asOf": snapshot["asOf"],
        "headline": "毛利率回升，维持 hold",
        "rating": {"stance": "hold", "conviction": 3, "horizon": "6-12m"},
        "summary": "核心结论：基本面稳定，估值处于中性区间，等待催化剂确认。",
        "keyMetrics": [{"label": "最新价", "value": "1,672.00"}],
        "sections": [
            {"id": section_id, "title": f"{section_id} 标题", "blocks": [{"type": "divider"}]}
            for section_id in SECTIONS
        ],
        "risks": [{"title": "需求不及预期", "severity": "medium", "trigger": "季度营收同比转负"}],
        "sources": [{"id": "S1", "title": "行情快照", "publisher": "akshare"}],
        "disclaimer": "本报告由 AI 生成，仅供研究参考，不构成投资建议。",
    }
    doc.update(overrides)
    return doc


def _base_doc(snapshot: dict[str, Any]) -> dict[str, Any]:
    """一遍**真实**的合法报告做基座（`tests/fixtures/sample_report.json`）。

    为什么不用手搓的最小文档：整层替换会弄丢必需兄弟字段（`instrument.name`、
    `keyMetrics[].label` …），于是"收敛失败"其实测的是夹具而不是 clamp。
    """
    sample = json.loads((FIXTURES / "sample_report.json").read_text(encoding="utf-8"))
    sample["asOf"] = snapshot["asOf"]
    return sample


def _assert_valid(doc: dict[str, Any]) -> None:
    jsonschema.validate(doc, rv.load_schema())


# ── AC-117：E-93 的夹具 ─────────────────────────────────────────────────────


def test_the_601_char_summary_fixture_now_validates(snapshot_payload: dict[str, Any]) -> None:
    """AC-117 的**先红后绿**本体：601 字的 summary 不再让整份报告消失。"""
    doc = _minimal_doc(snapshot_payload, summary="白" * 601)

    outcome = rv.validate_doc(copy.deepcopy(doc), snapshot_payload)
    assert outcome.errors == [], outcome.errors
    assert outcome.quality["clamp"] == {"count": 1, "fields": ["summary"]}
    assert outcome.status == "degraded", "截过的报告必须降级（读者有权知道）"
    clamped = outcome.doc["summary"]
    assert len(clamped) <= 600
    assert clamped.endswith("…")
    _assert_valid(outcome.doc)


def test_salvage_also_rescues_a_length_only_document(snapshot_payload: dict[str, Any]) -> None:
    """E-93 的 **B 分支**：`salvage_doc` 对同一份文档也返回 None，过去无人可救。

    修法不是给 salvage 加一条特例，而是把 clamp 挂在 `normalize_doc()` 里
    （C-34：它是唯一的修补入口）——于是两条路自动都通了。
    """
    doc = _minimal_doc(snapshot_payload, summary="白" * 601)
    rescued, report = rv.salvage_doc(copy.deepcopy(doc), snapshot_payload)
    assert rescued is not None, report.reasons
    assert report.clamped == 1
    _assert_valid(rescued)


def test_clamp_is_inside_normalize_doc_and_nowhere_else() -> None:
    """C-34：`clamp_doc()` 只能挂在 `normalize_doc()` 里。

    这条用例的理由很实际：如果哪天有人在 `validate_doc()` 里再补一次，先补的那次会被
    后面的覆盖（文档已被改），而 `clamp.count` 会数两遍——"截了 2 处"其实只有 1 处。
    """
    source = Path(rv.__file__).read_text(encoding="utf-8")
    assert source.count("clamp_doc(doc, repairs)") == 1
    assert source.count("def clamp_doc(") == 1, "clamp_doc 只能有一份实现"
    assert source.count("    clamp_doc(doc, repairs)") == 1, "只能有一个调用点（normalize_doc 末尾）"


# ── C-37：按块型分派 ─────────────────────────────────────────────────────────


def _block_limit(schema: dict[str, Any], kind: str, field: str) -> int | None:
    """schema 里**这个块型**的这个字段的上限（用例自己读，不抄常量）。"""
    defs = schema.get("$defs") or {}
    for variant in (defs.get("block") or {}).get("anyOf") or []:
        props = variant.get("properties") or {}
        types = props.get("type") or {}
        allowed = types.get("enum") or ([types["const"]] if "const" in types else [])
        if kind in allowed:
            spec = props.get(field) or {}
            return spec.get("maxLength") or spec.get("maxItems")
    return None


def test_block_text_limits_match_the_schema() -> None:
    """C-37：`BLOCK_TEXT_LIMITS` 必须与 schema 逐值相同（含 heading 的 120）。"""
    schema = rv.load_schema()
    for kind, expected in rv.BLOCK_TEXT_LIMITS.items():
        assert _block_limit(schema, kind, "text") == expected, f"{kind}.text 与 schema 不符"
    assert rv.BLOCK_TEXT_LIMITS["heading"] == 120
    assert rv.BLOCK_TEXT_LIMITS["paragraph"] == 4000


def test_a_long_heading_is_clamped_to_120_not_4000(snapshot_payload: dict[str, Any]) -> None:
    """AC-118 的专项：一个 402 字的 heading 必须被收敛到 **120**。

    "一刀切 4000"的实现会在这条用例上红：402 < 4000 ⇒ 不收敛 ⇒ `jsonschema.validate`
    报 `text is too long` ⇒ 整份报告消失（正是这一轮要修的那个失败形态）。
    """
    doc = _minimal_doc(snapshot_payload)
    doc["sections"][0]["blocks"] = [{"type": "heading", "level": 2, "text": "长标题" * 134}]

    outcome = rv.validate_doc(copy.deepcopy(doc), snapshot_payload)
    assert outcome.errors == [], outcome.errors
    text = outcome.doc["sections"][0]["blocks"][0]["text"]
    assert len(text) <= 120
    assert text.endswith("…")
    assert outcome.quality["clamp"]["fields"] == ["sections[0].blocks[0].text"]
    _assert_valid(outcome.doc)


# ── AC-118：遍历 schema 收集上界 ────────────────────────────────────────────


def _bounded_nodes() -> list[tuple[str, str, int]]:
    """schema 里每一个有界节点：`(规范路径, 约束名, 上限)`。

    路径用本仓文档的形状表达（`summary` / `sections[].blocks[]<heading>.text` / ...），
    与 `_clamp_node()` 拼出来的 `repairs` 路径同源。
    """
    schema = rv.load_schema()
    defs = schema.get("$defs") or {}
    out: list[tuple[str, str, int]] = []

    def walk(node: Any, path: str, kind: str | None) -> None:
        node = rv._resolve_schema(node, defs)
        if kind is not None:
            picked = rv._pick_variant(node, {"type": kind}, defs)
            if picked is not None:
                node = picked
        for constraint in ("maxLength", "maxItems", "maxProperties"):
            limit = node.get(constraint)
            if isinstance(limit, int):
                suffix = {"maxLength": "", "maxItems": "[]", "maxProperties": "{}"}[constraint]
                out.append((f"{path}{suffix}", constraint, limit))
        for name, spec in (node.get("properties") or {}).items():
            walk(spec, f"{path}.{name}" if path else name, None)
        if isinstance(node.get("items"), dict):
            # ★v12：`items` 解析后若本身是 `anyOf`（块型），路径由下面的 anyOf 分支
            # 拼 `[]<kind>`；这里再追加一个 `[]` 会得到 `blocks[][]` 这种不可达路径。
            resolved_items = rv._resolve_schema(node["items"], defs)
            fallthrough = isinstance(resolved_items.get("anyOf"), list)
            walk(node["items"], path if fallthrough else f"{path}[]", None)
        for candidate in node.get("anyOf") or []:
            resolved = rv._resolve_schema(candidate, defs)
            spec = (resolved.get("properties") or {}).get("type") or {}
            allowed = spec.get("enum") or ([spec["const"]] if "const" in spec else [])
            if not allowed:
                continue
            walk(candidate, f"{path}[]<{allowed[0]}>", allowed[0])

    walk(schema, "", None)
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, int]] = []
    for entry in out:
        if (entry[0], entry[1]) in seen:
            continue
        seen.add((entry[0], entry[1]))
        unique.append(entry)
    return unique


def test_the_schema_really_has_dozens_of_bounds() -> None:
    """门槛断言：扫描器静默失效（返回 3 条）时下面那条用例会假绿。"""
    nodes = _bounded_nodes()
    assert len(nodes) >= 60, f"只扫到 {len(nodes)} 个有界节点，扫描器可能坏了"


def _value_for(constraint: str, limit: int, *, path: str = "") -> Any:
    """一个刚好越界 1 个单位的值，形状按落点决定。"""
    if constraint == "maxLength":
        return "白" * (limit + 1)
    if constraint == "maxItems":
        name = path.replace("[]", "").rsplit(".", 1)[-1]
        templates: dict[str, Any] = {
            "keyMetrics": lambda count: [{"label": f"指标{i}", "value": str(i)} for i in range(count)],
            "columns": lambda count: [{"key": "k", "label": "列"} for _ in range(count)],
            "rows": lambda count: [{"k": str(i)} for i in range(count)],
            "sections": lambda count: [
                {"id": SECTIONS[i % len(SECTIONS)], "title": "章节", "blocks": [{"type": "divider"}]}
                for i in range(count)
            ],
            "series": lambda count: [{"name": "s", "data": [1.0]} for _ in range(count)],
            "risks": lambda count: [
                {"title": f"风险{i}", "severity": "medium", "trigger": "触发"} for i in range(count)
            ],
            "sources": lambda count: [{"id": f"S{i}", "title": "来源", "publisher": "p"} for i in range(count)],
            "events": lambda count: [{"date": "2026-01-01", "title": "事件"} for _ in range(count)],
            "peers": lambda count: [{"name": "同业", "symbol": "000858.SZ"} for _ in range(count)],
            "scenarios": lambda count: [
                {"name": "base", "targetPrice": 1700.0, "probability": 0.5, "assumptions": "假设"}
                for _ in range(count)
            ],
            "data": lambda count: [1.0] * count,
            "labels": lambda count: [f"标签{i}" for i in range(count)],
            "refs": lambda count: ["S1"] * count,
            "sourceRefs": lambda count: ["S1"] * count,
        }
        if name == "items":
            # `bullets.items` 的元素是 `{text}`；`metrics.items` 的元素是 `{label, value}`。
            if "<bullets>" in path:
                return [{"text": f"条目{i}"} for i in range(limit + 1)]
            return [{"label": "指标", "value": str(i)} for i in range(limit + 1)]
        factory = templates.get(name)
        if factory is not None:
            return factory(limit + 1)
        return ["S1"] * (limit + 1)
    return {f"k{index}": str(index) for index in range(limit + 1)}


def _compile(path: str) -> tuple[list[tuple[str, str | None]], bool]:
    """规范路径 → `([("key"|"index", 值)], 是否是"列表本身")`。

    `keyMetrics[]` 是"**列表本身**"（maxItems 落在 `keyMetrics` 上），而
    `keyMetrics[].label` 是"元素里的字段"——两者差一个操作。
    """
    kind = None
    if "<" in path:
        start, end = path.index("<"), path.index(">")
        kind = path[start + 1 : end]
        path = path[:start] + path[end + 1 :]
    is_list_itself = path.endswith("[]")
    if is_list_itself:
        path = path[:-2]
    ops: list[tuple[str, str | None]] = []
    for part in path.split("."):
        name, _, bracket = part.partition("[")
        if name:
            ops.append(("key", name))
        if bracket:
            ops.append(("index", kind if name == "blocks" else None))
    return ops, is_list_itself


def _skeleton(name: str, kind: str | None) -> dict[str, Any]:
    """缺失元素的补位模板（只为"合法"服务，不追求内容）。"""
    if kind and name == "blocks":
        return dict(BLOCK_TEMPLATE[kind])
    return {
        "instrument": {"symbol": "600519.SH", "name": "贵州茅台", "market": "CN", "currency": "CNY"},
        "rating": {"stance": "hold", "conviction": 3, "horizon": "6-12m"},
        "keyMetrics": {"label": "指标", "value": "1.0"},
        "risks": {"title": "风险", "severity": "medium", "trigger": "触发"},
        "sources": {"id": "S1", "title": "来源", "publisher": "p"},
        "sections": {"id": SECTIONS[0], "title": "章节", "blocks": [{"type": "divider"}]},
        "items": {"label": "指标", "value": "1.0"},
        "columns": {"key": "k", "label": "列"},
        "rows": {"k": "1"},
        "events": {"date": "2026-01-01", "title": "事件"},
        "peers": {"name": "同业", "symbol": "000858.SZ"},
        "scenarios": {"name": "base", "targetPrice": 1700.0, "probability": 0.5, "assumptions": "假设"},
    }.get(name, {})


def _compile(path: str) -> tuple[list[tuple[str, str | None]], bool]:
    """规范路径 → `([("key"|"index", 值)], 末尾是否 `[]`)`。

    ★v12：`[]` 有**两种**含义，靠约束名区分（见 `_plant`）：
    * `maxItems` / `maxProperties` ⇒ "列表本身"；
    * `maxLength`（元素上的）⇒ "第 0 个元素"。
    """
    kind = None
    normalized = path
    if "<" in normalized:
        start, end = normalized.index("<"), normalized.index(">")
        kind = normalized[start + 1 : end]
        normalized = normalized[:start] + normalized[end + 1 :]
    trailing_list = normalized.endswith("[]")
    if trailing_list:
        normalized = normalized[:-2]
    ops: list[tuple[str, str | None]] = []
    for part in normalized.split("."):
        name, _, bracket = part.partition("[")
        if name:
            ops.append(("key", name))
        if bracket:
            ops.append(("index", kind if name == "blocks" else None))
    if trailing_list:
        ops.append(("index", None))
    return ops, trailing_list


def _skeleton(name: str, kind: str | None) -> dict[str, Any]:
    """缺失元素的补位模板（只为"合法"服务，不追求内容）。"""
    if kind and name == "blocks":
        return dict(BLOCK_TEMPLATE[kind])
    return {
        "instrument": {"symbol": "600519.SH", "name": "贵州茅台", "market": "CN", "currency": "CNY"},
        "rating": {"stance": "hold", "conviction": 3, "horizon": "6-12m"},
        "keyMetrics": {"label": "指标", "value": "1.0"},
        "risks": {"title": "风险", "severity": "medium", "trigger": "触发"},
        "sources": {"id": "S1", "title": "来源", "publisher": "p"},
        "sections": {"id": SECTIONS[0], "title": "章节", "blocks": [{"type": "divider"}]},
        "items": {"label": "指标", "value": "1.0"},
        "columns": {"key": "k", "label": "列"},
        "rows": {"k": "1"},
        "events": {"date": "2026-01-01", "title": "事件"},
        "peers": {"name": "同业", "symbol": "000858.SZ"},
        "scenarios": {"name": "base", "targetPrice": 1700.0, "probability": 0.5, "assumptions": "假设"},
    }.get(name, {})


def _descend(doc: dict[str, Any], ops: list[tuple[str, str | None]], path: str) -> Any:
    """沿 `ops` 降到**父容器**，缺的节点按"下一段的形状"补位。

    ★v12 的关键一条：经过 `blocks[]<kind>` 时把第 0 块**替换**成那种块型的模板。
    不替换的话，样例报告里的 heading 会被塞进 `items` 字段 —— 那时失败的是夹具，
    不是 clamp（前六版就是在这里自我欺骗的）。
    """
    cursor: Any = doc
    for position, (op, extra) in enumerate(ops):
        nxt = ops[position + 1] if position + 1 < len(ops) else None
        if op == "key":
            name = str(extra)
            if not isinstance(cursor.get(name), (dict, list)):
                cursor[name] = {} if (nxt is None or nxt[0] == "key") else []
            cursor = cursor[name]
            continue
        assert isinstance(cursor, list), f"{path}: 期望列表，实际 {type(cursor).__name__}"
        if not cursor:
            cursor.append({})
        if extra is not None:
            # 块型分派：整块换成本块型的最小合法形状。
            cursor[0] = {**BLOCK_TEMPLATE[extra], **({"items": cursor[0].get("items")} if False else {})}
        elif nxt is not None and nxt[0] == "key" and isinstance(cursor[0], dict) and nxt[1] not in cursor[0]:
            cursor[0].update(_skeleton(str(nxt[1]), None))
        cursor = cursor[0]
    return cursor


def _plant(doc: dict[str, Any], path: str, value: Any, constraint: str) -> None:
    """把越界值放到规范路径上，**只改目标**（兄弟字段原样保留）。"""
    ops, trailing_list = _compile(path)
    if trailing_list and constraint in ("maxItems", "maxProperties"):
        ops = ops[:-1]  # 说的是"列表本身"
    assert ops, f"{path}: 空路径"
    parent_ops, last = ops[:-1], ops[-1]
    cursor = _descend(doc, parent_ops, path)
    op, name = last
    if op == "index":
        # 元素级上界（例如 `sourceRefs[]` 里那个 item 的 maxLength）：改第 0 个元素。
        assert isinstance(cursor, list), f"{path}: 期望列表"
        if not cursor:
            cursor.append("")
        cursor[0] = value
        return
    assert isinstance(cursor, dict), f"{path}: 期望对象，实际 {type(cursor).__name__}"
    cursor[str(name)] = value


def test_every_bounded_node_can_be_clamped(snapshot_payload: dict[str, Any]) -> None:
    """AC-118 的主体：**每一个** schema 上界都能收敛，且收敛后仍然合法。

    做法不是"给每个字段写一条用例"（那会随 schema 漂移），而是**遍历 schema 自己**
    收集节点 —— 下一次加字段时这条用例自动覆盖它，而手写清单不会。
    """
    nodes = _bounded_nodes()
    assert nodes, "扫描器没找到任何上界"
    failures: list[str] = []
    skipped: list[str] = []
    checked = 0
    for path, constraint, limit in nodes:
        doc = _base_doc(snapshot_payload)
        try:
            _plant(doc, path, _value_for(constraint, limit, path=path), constraint)
        except (KeyError, IndexError, TypeError, ValueError, AssertionError) as exc:
            # 落不了点 = **夹具**覆盖不到（样例报告里那个列表恰好是空的），不是 clamp 的错。
            # 单独计数并设上限：静默塌陷（只剩 3 条）必须能被发现。
            skipped.append(f"{path}: {exc}")
            continue
        outcome = rv.validate_doc(copy.deepcopy(doc), snapshot_payload)
        checked += 1
        if outcome.errors:
            failures.append(f"{path}: 收敛后仍不合法 -> {outcome.errors[:1]}")
            continue
        try:
            _assert_valid(outcome.doc)
        except jsonschema.ValidationError as exc:  # pragma: no cover - 收敛的硬 bug
            failures.append(f"{path}: 收敛后 jsonschema 失败 -> {exc.message[:80]}")
    assert checked >= 40, f"只真正验证了 {checked} 个上界（跳过 {len(skipped)}）"
    assert len(skipped) <= 30, f"夹具覆盖塌陷：{len(skipped)} 个节点没能落点\n" + "\n".join(skipped[:8])
    assert not failures, "以下上界没能被收敛：\n" + "\n".join(failures[:12])


# ── 截断的纪律（R-56 / V11-P2-4） ──────────────────────────────────────────


def test_clamp_never_cuts_a_number_in_half() -> None:
    """R-56：截断不许切在数字中间。

    `-1,509.76` 切成 `-1.` 会凭空造出一个未接地 claim —— 那等于用"修格式"的方式
    制造一个"事实错误"。这条用例用一段**只有数字和标点**的文本逼它走到兜底分支，
    并且专门挡住"千分位逗号被当成句末"这个坑（ASCII 逗号也在句界表里）。
    """
    text = "，".join(["1,509.76"] * 100)  # ~900 字
    clamped = rv._clamp_text(text, 120)
    assert len(clamped) <= 120
    assert clamped.endswith("…")
    body = clamped[:-1].rstrip("，, ")
    assert not body.endswith((",", ".")), repr(clamped[-12:])
    # 每一个残留的数字片段都必须是**能解析的**完整数（被腰斩的 `1,` 会在这里抛错）。
    for token in body.split("，"):
        if token.strip():
            float(token.replace(",", ""))


def test_clamp_respects_min_length_and_leaves_short_text_alone() -> None:
    """截断不能把文本切到 `minLength` 以下（`summary.minLength = 10`）。"""
    assert rv._clamp_text("短文本", 600) == "短文本"
    clamped = rv._clamp_text("白" * 601, 600)
    assert 10 <= len(clamped) <= 600
    assert clamped.endswith("…")


# ── 记录与契约 ─────────────────────────────────────────────────────────────


def test_quality_clamp_key_has_a_response_field() -> None:
    """C-48：`quality.clamp` 是新增键，`ReportQuality` 必须声明它（反射守卫）。"""
    from app.schemas.report import ClampQuality, ReportQuality

    assert "clamp" in ReportQuality.model_fields
    assert set(ClampQuality.model_fields) == {"count", "fields"}


def test_salvage_payload_now_carries_the_keys_the_frontend_needs() -> None:
    """V11-P2-2：`as_payload()` 过去只发 5 个键，**漏了** `prunedBlocks`。

    写进一个前端读不到的地方等于没写。三处同步：报告键、响应模型、`web/lib/types.ts`。
    """
    from app.schemas.report import SalvageQuality

    payload = rv.SalvageReport(used=True, droppedKeys=1, prunedBlocks=2, clamped=1, how="json_repaired").as_payload()
    for key in ("used", "droppedKeys", "droppedSections", "droppedBlocks", "prunedBlocks", "clamped", "how", "reasons"):
        assert key in payload, f"salvage 载荷缺 {key}"
    assert set(payload) - {"reasons"} <= set(SalvageQuality.model_fields)


def test_the_clamp_record_is_machine_readable(snapshot_payload: dict[str, Any]) -> None:
    """`quality.clamp = {count, fields}`；`count` 是**字段数**而不是"改动次数"。"""
    doc = _minimal_doc(snapshot_payload, summary="白" * 601, headline="头" * 200)
    outcome = rv.validate_doc(copy.deepcopy(doc), snapshot_payload)
    clamp = outcome.quality["clamp"]
    assert clamp["count"] == 2
    assert sorted(clamp["fields"]) == ["headline", "summary"]
    # 反面：没有越界就没有这个键（老报告读作 `{count: 0}`，不回填）。
    clean = rv.validate_doc(_minimal_doc(snapshot_payload), snapshot_payload)
    assert "clamp" not in clean.quality


def test_repairs_carry_the_prefix_run_manager_uses() -> None:
    """`run_manager` 按 `repair.startswith("收敛")` 打 `run.log{code:"clamp"}`。

    这行字符串是两个模块之间唯一的约定，所以它需要一个断言（否则改名只会在界面上
    悄悄失去那行提示）。
    """
    repairs: list[str] = []
    doc = {"summary": "白" * 601}
    rv.clamp_doc(doc, repairs)
    assert repairs and all(item.startswith(rv.CLAMP_PREFIX) for item in repairs)
    assert rv.CLAMP_PREFIX == "收敛"
    assert json.dumps(rv.clamp_payload(repairs), ensure_ascii=False) == '{"count": 1, "fields": ["summary"]}'
