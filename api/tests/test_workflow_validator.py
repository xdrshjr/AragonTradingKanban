"""研究流程校验器（spec §3.4 / AC-W9）：每个错误码 / 警告码至少一个用例，空白图特判，
预算估算只是警告，内容哈希的规范化（RW-10）。"""

from __future__ import annotations

from workflow_factory import ALL_SECTIONS, blank_doc, chain_doc, covering_step, edge, node, step, valid_doc

from app.schemas.workflow import Edge, Policy, WorkflowDoc
from app.services import workflow_contract as C
from app.services.workflow_validator import (
    Ceilings,
    analyze,
    content_hash,
    default_ceilings,
    estimated_turns,
    validate,
)

CEILINGS = Ceilings(max_turns=40, max_duration_minutes=15)


def codes(doc: WorkflowDoc, **kwargs) -> list[str]:  # type: ignore[no-untyped-def]
    return [issue.code for issue in validate(doc, ceilings=CEILINGS, **kwargs).issues]


def test_valid_doc_is_publishable() -> None:
    report = validate(valid_doc(), ceilings=CEILINGS)
    assert report.errorCount == 0 and report.publishable and not report.blank


# ── errors ───────────────────────────────────────────────────────────────────


def test_no_start_and_multiple_start() -> None:
    doc = chain_doc(covering_step())
    no_start = WorkflowDoc(nodes=[n for n in doc.nodes if n.kind != "start"], edges=[])
    assert "no_start" in codes(no_start)
    doubled = chain_doc(covering_step(), node("start2", "start"))
    assert "multiple_start" in codes(doubled)


def test_no_end_when_missing_or_not_exactly_one() -> None:
    doc = chain_doc(covering_step())
    without_end = WorkflowDoc(nodes=[n for n in doc.nodes if n.kind != "end"], edges=[])
    assert "no_end" in codes(without_end)
    assert "no_end" in codes(chain_doc(covering_step(), node("end2", "end")))


def test_no_step_for_a_graph_with_only_a_gate() -> None:
    assert "no_step" in codes(chain_doc(node("g1", "gate")))


def test_cycle_reports_its_path() -> None:
    doc = chain_doc(covering_step("a"), step("b"), extra_edges=[edge("b", "a")])
    report = validate(doc, ceilings=CEILINGS)
    cycle = next(i for i in report.issues if i.code == "cycle")
    assert cycle.entityKind == "graph"
    assert cycle.params["path"][0] == cycle.params["path"][-1]
    assert not report.publishable


def test_self_loop_duplicate_and_dangling_edges() -> None:
    base = chain_doc(covering_step("a"))
    assert "self_loop" in codes(chain_doc(covering_step("a"), extra_edges=[edge("a", "a", "e_self")]))
    assert "duplicate_edge" in codes(chain_doc(covering_step("a"), extra_edges=[edge("a", "end", "e_dup")]))
    dangling = WorkflowDoc(nodes=base.nodes, edges=[*base.edges, Edge(id="e_x", source="a", target="ghost")])
    issue = next(i for i in validate(dangling, ceilings=CEILINGS).issues if i.code == "dangling_edge")
    assert issue.entityId == "e_x" and issue.params["missing"] == "ghost"


def test_note_cannot_take_part_in_edges() -> None:
    doc = chain_doc(covering_step("a"), node("n1", "note", body="x"), extra_edges=[edge("a", "n1")])
    assert "note_has_edge" in codes(doc)


def test_start_input_and_end_output_directions() -> None:
    doc = chain_doc(covering_step("a"), extra_edges=[edge("a", "start", "e_back"), edge("end", "a", "e_out")])
    found = codes(doc)
    assert "start_has_input" in found and "end_has_output" in found


def test_unreachable_step_and_gate() -> None:
    doc = chain_doc(covering_step("a"))
    doc.nodes.append(step("orphan"))
    doc.nodes.append(node("g9", "gate"))
    doc.edges.append(edge("orphan", "end", "e_o"))
    report = validate(doc, ceilings=CEILINGS)
    unreachable = {i.entityId for i in report.issues if i.code == "unreachable_node"}
    assert unreachable == {"orphan", "g9"}


def test_empty_prompt_blocks_publishing() -> None:
    report = validate(chain_doc(covering_step("a", prompt="   ")), ceilings=CEILINGS)
    assert "empty_prompt" in [i.code for i in report.issues] and not report.publishable


def test_files_and_sections_must_come_from_the_whitelists() -> None:
    doc = chain_doc(covering_step("a", files=["snapshot.json", "../etc/passwd"], sections=[*ALL_SECTIONS, "appendix"]))
    report = validate(doc, ceilings=CEILINGS)
    by_code = {i.code: i for i in report.issues}
    assert by_code["invalid_file"].params["value"] == "../etc/passwd"
    assert by_code["invalid_section"].params["value"] == "appendix"


def test_section_not_covered_names_the_missing_section() -> None:
    report = validate(chain_doc(step("a", sections=["thesis", "risks"])), ceilings=CEILINGS)
    missing = {i.params["section"] for i in report.issues if i.code == "section_not_covered"}
    assert missing == {"fundamentals", "valuation", "catalysts"}


def test_section_served_only_by_an_unreachable_step_is_not_covered() -> None:
    doc = chain_doc(step("a", sections=["thesis", "fundamentals", "valuation", "risks"]))
    doc.nodes.append(step("lost", sections=["catalysts"]))
    assert "section_not_covered" in codes(doc)


def test_undefined_placeholder_but_not_escaped_or_known_ones() -> None:
    bad = chain_doc(covering_step("a", prompt="看 {{instrument.symbol}} 与 {{ foo }} 与 \\{{literal}}"))
    report = validate(bad, ceilings=CEILINGS)
    names = [i.params["name"] for i in report.issues if i.code == "undefined_placeholder"]
    assert names == ["foo"]
    assert "undefined_placeholder" not in codes(chain_doc(covering_step("a", prompt="{{asOf}} {{timeframe}}")))


def test_undefined_placeholder_in_gate_checklist() -> None:
    doc = chain_doc(covering_step("a"), node("g1", "gate", checklist="核对 {{nope}}"))
    assert "undefined_placeholder" in codes(doc)


def test_node_edge_and_step_limits() -> None:
    many_steps = [step(f"s{i}", sections=ALL_SECTIONS) for i in range(C.MAX_STEPS + 1)]
    found = codes(chain_doc(*many_steps))
    assert "step_limit_exceeded" in found
    many_notes = [node(f"n{i}", "note", body="x") for i in range(C.MAX_NODES)]
    assert "node_limit_exceeded" in codes(chain_doc(covering_step("a"), *many_notes))
    doc = chain_doc(covering_step("a"))
    doc.edges.extend(edge("start", "end", f"e_x{i}") for i in range(C.MAX_EDGES))
    assert "edge_limit_exceeded" in codes(doc)


def test_doc_too_large() -> None:
    notes = [node(f"n{i}", "note", body="汉" * C.NOTE_MAX) for i in range(30)]
    assert "doc_too_large" in codes(chain_doc(covering_step("a"), *notes))


def test_policy_out_of_range_only_tightens() -> None:
    assert "policy_out_of_range" in codes(chain_doc(covering_step("a"), policy=Policy(maxTurns=41)))
    assert "policy_out_of_range" in codes(chain_doc(covering_step("a"), policy=Policy(maxTurns=0)))
    assert "policy_out_of_range" in codes(chain_doc(covering_step("a"), policy=Policy(maxDurationMinutes=16)))
    assert "policy_out_of_range" not in codes(
        chain_doc(covering_step("a"), policy=Policy(maxTurns=30, maxDurationMinutes=10))
    )


def test_plan_too_large_is_an_error_and_plan_large_a_warning() -> None:
    report = validate(valid_doc(), ceilings=CEILINGS, plan_bytes=C.MAX_PLAN_BYTES + 1)
    assert "plan_too_large" in [i.code for i in report.issues] and not report.publishable
    report = validate(valid_doc(), ceilings=CEILINGS, plan_bytes=C.WARN_PLAN_BYTES + 1)
    assert "plan_large" in [i.code for i in report.issues] and report.publishable


# ── warnings ─────────────────────────────────────────────────────────────────


def test_warnings_do_not_block_publishing() -> None:
    doc = chain_doc(covering_step("a", prompt="做事", successCriteria=""), node("g1", "gate"))
    report = validate(doc, ceilings=CEILINGS)
    found = {i.code for i in report.issues}
    assert {"missing_success_criteria", "empty_gate"} <= found
    assert report.publishable and report.errorCount == 0


def test_node_not_reaching_end() -> None:
    doc = chain_doc(covering_step("a"))
    doc.nodes.append(step("dead"))
    doc.edges.append(edge("a", "dead", "e_dead"))
    report = validate(doc, ceilings=CEILINGS)
    assert {i.entityId for i in report.issues if i.code == "node_not_reaching_end"} == {"dead"}


def test_orphan_note_is_an_empty_note() -> None:
    doc = chain_doc(covering_step("a"), node("n1", "note", body=" "), node("n2", "note", body="港股注意币种"))
    assert {i.entityId for i in validate(doc, ceilings=CEILINGS).issues if i.code == "orphan_note"} == {"n1"}


def test_high_fanout() -> None:
    fan = [step(f"f{i}", sections=ALL_SECTIONS) for i in range(C.HIGH_FANOUT + 1)]
    doc = chain_doc(covering_step("a"), *[])
    doc.nodes.extend(fan)
    for f in fan:
        doc.edges.extend([edge("a", f.id), edge(f.id, "end")])
    assert "high_fanout" in codes(doc)


def test_long_prompt() -> None:
    assert "long_prompt" in codes(chain_doc(covering_step("a", prompt="长" * (C.LONG_PROMPT + 1))))


def test_isolated_step() -> None:
    doc = chain_doc(covering_step("a"))
    doc.nodes.append(step("lonely"))
    found = codes(doc)
    assert "isolated_step" in found and "unreachable_node" in found


def test_duplicate_title() -> None:
    doc = chain_doc(
        covering_step(
            "a",
        ),
        step("b"),
    )
    doc.nodes[1].title = doc.nodes[2].title = "同名"
    assert codes(doc).count("duplicate_title") == 2


def test_budget_tight_is_a_warning_and_counts_the_reading_turn() -> None:
    heavy = [step(f"d{i}", effort="deep", sections=ALL_SECTIONS) for i in range(4)]
    doc = chain_doc(*heavy)
    assert estimated_turns(doc) == 1 + 4 * 8
    report = validate(doc, ceilings=CEILINGS)
    tight = next(i for i in report.issues if i.code == "budget_tight")
    assert tight.severity == "warning" and report.publishable
    assert tight.params["estimated"] == 33
    assert "budget_tight" not in codes(chain_doc(covering_step("a")))
    # a tightened policy lowers the effective ceiling
    assert "budget_tight" in codes(chain_doc(covering_step("a"), step("b"), policy=Policy(maxTurns=8)))


def test_gate_weighs_one_turn() -> None:
    assert estimated_turns(chain_doc(covering_step("a"), node("g1", "gate"))) == 1 + 4 + 1


# ── blank graph ──────────────────────────────────────────────────────────────


def test_blank_graph_is_not_a_wall_of_red_errors() -> None:
    report = validate(blank_doc(), ceilings=CEILINGS)
    assert report.blank and report.errorCount == 0
    assert not report.publishable  # nothing to publish yet
    assert "no_step" not in [i.code for i in report.issues]
    assert "section_not_covered" not in [i.code for i in report.issues]


def test_graph_with_a_gate_only_is_not_blank() -> None:
    report = validate(chain_doc(node("g1", "gate")), ceilings=CEILINGS)
    assert not report.blank and "no_step" in [i.code for i in report.issues]


# ── analysis ─────────────────────────────────────────────────────────────────


def test_levels_use_longest_path_and_document_order() -> None:
    a, b, c, d = step("a"), step("b"), step("c"), step("d")
    from app.schemas.workflow import WorkflowDoc as Doc

    doc = Doc(
        nodes=[node("start", "start"), a, b, c, d, node("end", "end")],
        edges=[
            edge("start", "a"),
            edge("start", "b"),
            edge("a", "c"),
            edge("b", "c"),
            edge("c", "d"),
            edge("d", "end"),
        ],
    )
    analysis = analyze(doc)
    assert [analysis.levels[k] for k in "abcd"] == [1, 1, 2, 3]
    assert analysis.levels["start"] == 0 and "end" not in analysis.levels
    assert analysis.cycle == ()


def test_analysis_ignores_note_and_invalid_edges() -> None:
    doc = chain_doc(
        covering_step("a"), node("n1", "note", body="x"), extra_edges=[edge("a", "n1"), edge("a", "a", "e_s")]
    )
    analysis = analyze(doc)
    assert all(e.target != "n1" and e.source != e.target for e in analysis.edges)


# ── content hash (§3.7, RW-10) ───────────────────────────────────────────────


def test_hash_ignores_position_viewport_rev_notes_and_order() -> None:
    doc = chain_doc(covering_step("a"), step("b"))
    before = content_hash(doc)
    moved = doc.model_copy(deep=True)
    moved.nodes[1].position.x = 999
    moved.viewport.zoom = 2
    moved.nodes[1].rev = 9
    moved.nodes.append(node("n1", "note", body="随便写"))
    assert content_hash(moved) == before
    reordered = doc.model_copy(deep=True)
    reordered.nodes.reverse()
    reordered.edges.reverse()
    assert content_hash(reordered) == before


def test_hash_changes_with_content_and_treats_null_like_absent() -> None:
    doc = chain_doc(covering_step("a"))
    before = content_hash(doc)
    edited = doc.model_copy(deep=True)
    edited.nodes[1].config["prompt"] = "换一句话"
    assert content_hash(edited) != before
    with_null = doc.model_copy(deep=True)
    with_null.nodes[1].config["effort"] = None
    with_null.nodes[1].config["files"] = []
    assert content_hash(with_null) == before
    retitled = doc.model_copy(deep=True)
    retitled.nodes[1].title = "新标题"
    assert content_hash(retitled) != before


def test_hash_is_twelve_hex_chars_and_stable() -> None:
    value = content_hash(valid_doc())
    assert len(value) == 12 and int(value, 16) >= 0
    assert content_hash(valid_doc()) == value


def test_default_ceilings_follow_settings() -> None:
    ceilings = default_ceilings()
    assert ceilings.max_turns >= 1 and ceilings.max_duration_minutes >= 1
