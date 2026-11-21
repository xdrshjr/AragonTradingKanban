"""进度标记解析（spec §3.6.3 / AC-W15）：文法、跨 delta 切分、幂等、乱序、未知键、缓冲上限、
随机分块等价于整串、`strip_markers`。标记只是观测——这里没有任何一条会让 run 失败。"""

from __future__ import annotations

import random

from app.services.workflow_trace import TraceParser, strip_markers

KEYS = {"S1": "n_a", "S2": "n_b", "G1": "n_g"}


def run(parts: list[str]) -> tuple[str, list[tuple[str, str]], TraceParser]:
    parser = TraceParser(KEYS)
    texts: list[str] = []
    events: list[tuple[str, str]] = []
    for part in parts:
        chunk = parser.feed(part)
        texts.append(chunk.text)
        events.extend((e.key, e.state) for e in chunk.events)
    tail = parser.finish()
    texts.append(tail.text)
    events.extend((e.key, e.state) for e in tail.events)
    return "".join(texts), events, parser


def test_whole_marker_is_removed_and_reported() -> None:
    text, events, _ = run(["先读资料\n[[wf:S1:start]]\n继续分析\n[[wf:S1:done]]\n结束"])
    assert text == "先读资料\n继续分析\n结束"
    assert events == [("S1", "start"), ("S1", "done")]


def test_marker_split_across_deltas_is_not_lost_nor_leaked() -> None:
    text, events, _ = run(["a\n[[wf:S", "2:do", "ne]]\nb"])
    assert "[[" not in text and "wf" not in text
    assert text == "a\nb"
    assert events == [("S2", "start"), ("S2", "done")]


def test_skip_keeps_its_reason_note() -> None:
    parser = TraceParser(KEYS)
    chunk = parser.feed("[[wf:S1:skip 数据不足，已在 summary 说明]]\n")
    assert chunk.events[0].state == "skip"
    assert chunk.events[0].note == "数据不足，已在 summary 说明"
    assert chunk.events[0].node_id == "n_a"
    assert chunk.text == ""


def test_done_without_start_implies_start() -> None:
    _, events, parser = run(["[[wf:S1:done]]"])
    assert events == [("S1", "start"), ("S1", "done")]
    assert parser.summary().started_keys == ("S1",)


def test_repeated_markers_are_idempotent() -> None:
    _, events, parser = run(["[[wf:S1:start]]x[[wf:S1:start]]y[[wf:S1:done]][[wf:S1:done]]"])
    assert events == [("S1", "start"), ("S1", "done")]
    summary = parser.summary()
    assert (summary.started, summary.done, summary.skipped) == (1, 1, 0)
    assert summary.markers_seen == 4  # duplicates still count as seen


def test_out_of_order_and_interleaved_steps_are_all_accepted() -> None:
    _, events, parser = run(["[[wf:S2:start]][[wf:S1:start]][[wf:S2:done]][[wf:G1:done]][[wf:S1:done]]"])
    assert [e for e in events if e[1] == "done"] == [("S2", "done"), ("G1", "done"), ("S1", "done")]
    assert set(parser.summary().done_keys) == {"S1", "S2", "G1"}


def test_unknown_keys_are_counted_and_stripped() -> None:
    text, events, parser = run(["x[[wf:S9:done]]y"])
    assert text == "xy"
    assert events == []
    summary = parser.summary()
    assert summary.unknown_markers == 1
    assert summary.markers_seen == 0


def test_text_without_markers_passes_through_untouched() -> None:
    sample = "收入 [S1] 增长 12%，见 [[参考]] 与 [链接](x)\n第二行 [ 单括号"
    text, events, _ = run([sample])
    assert text == sample and events == []


def test_incomplete_tail_is_flushed_as_text_on_finish() -> None:
    text, _, _ = run(["结尾 [[wf:S1:do"])
    assert text == "结尾 [[wf:S1:do"


def test_buffer_is_bounded_and_overlong_garbage_is_flushed() -> None:
    garbage = "[[wf:" + "x" * 300
    parser = TraceParser(KEYS)
    chunk = parser.feed(garbage)
    assert chunk.text == garbage  # never held back beyond the buffer cap
    assert parser.feed("tail").text == "tail"


def test_malformed_marker_is_plain_text_not_a_crash() -> None:
    text, events, _ = run(["[[wf:S1:finished]] [wf:S1:done] 【wf:S1:done】"])
    assert text == "[[wf:S1:finished]] [wf:S1:done] 【wf:S1:done】"
    assert events == []


def test_inline_marker_is_removed_without_eating_the_line() -> None:
    text, _, _ = run(["完成 [[wf:S1:done]] 继续\n下一行"])
    assert text == "完成  继续\n下一行"


def test_pure_marker_delta_yields_empty_text() -> None:
    parser = TraceParser(KEYS)
    assert parser.feed("[[wf:S1:start]]").text == ""


def test_random_chunking_equals_whole_string() -> None:
    rng = random.Random(20261120)
    pieces = [
        "段落一\n",
        "[[wf:S1:start]]\n",
        "[[wf:S1:done]]",
        "[[wf:S2:skip a [b]]]\n",
        "[ [",
        "[[",
        "wf:",
        "]] ",
        "尾巴",
    ]
    for _ in range(200):
        whole = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 14)))
        expected_text, expected_events, expected_parser = run([whole])
        cuts = sorted(rng.sample(range(len(whole) + 1), k=min(len(whole) + 1, rng.randint(0, 6))))
        parts = [whole[a:b] for a, b in zip([0, *cuts], [*cuts, len(whole)], strict=True)]
        text, events, parser = run(parts)
        assert text == expected_text, (whole, parts)
        assert events == expected_events, (whole, parts)
        assert parser.summary() == expected_parser.summary()


def test_strip_markers_matches_the_streaming_result() -> None:
    raw = '前文\n[[wf:S1:start]]\n{"a":1}\n[[wf:S1:done]]\n[[wf:S7:done]]\n'
    assert strip_markers(raw) == '前文\n{"a":1}\n'
    assert "[[wf:" not in strip_markers("x [[wf:S2:skip 原因]] y")
    assert strip_markers("") == ""
    assert strip_markers("没有标记") == "没有标记"


def test_strip_markers_keeps_json_parseable() -> None:
    import json

    raw = '[[wf:S1:start]]\n{"summary": "ok", "n": [1, 2]}\n[[wf:S1:done]]'
    assert json.loads(strip_markers(raw)) == {"summary": "ok", "n": [1, 2]}


def test_summary_counts_are_per_key_and_finished_keys_dedupe() -> None:
    _, _, parser = run(["[[wf:S1:skip x]][[wf:S1:done]][[wf:S2:done]]"])
    summary = parser.summary()
    assert summary.finished_keys == ("S1", "S2")
    assert summary.skipped == 1 and summary.done == 2
