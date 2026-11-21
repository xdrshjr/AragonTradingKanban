"""提示词预算守卫（spec §3.5.4 / R-W3 / AC-W13）。

`assert_prompt_budget` 把 `prompt.md + system.md + schema.json` 合计限制在 32 768 B。流程只允许内联
≤2 KB 的索引（正文放 `workflow.md`，不占预算）。最坏情形同时叠加 v14 已有的 `base_digest`（≤2 048 B）
（RW-13）。这条测试读磁盘上**当前**的 system / task / schema 字节数：若 system 或 task 后续增长
使最坏余量 < 3 KB，这里变红就是"必须收紧索引上限"的信号——**不得**靠调大
`ARAGON_MAX_PROMPT_BYTES` 消化。
"""

from __future__ import annotations

from pathlib import Path

from workflow_factory import ALL_SECTIONS, chain_doc, step

from app.config import get_settings
from app.services import workflow_contract as C
from app.services.workflow_compiler import compile_workflow, sample_context

AGENT = Path(__file__).resolve().parents[1] / "app" / "agent"
SYSTEM = AGENT / "prompts" / "research_system.md"
TASK = AGENT / "prompts" / "research_task.md.j2"
SCHEMA = AGENT / "schemas" / "report_doc.schema.json"
BASE_DIGEST_MAX = 2048
REQUIRED_HEADROOM = 3 * 1024


def _fixed_bytes() -> int:
    return sum(path.stat().st_size for path in (SYSTEM, TASK, SCHEMA))


def test_worst_case_prompt_keeps_three_kib_of_headroom() -> None:
    limit = get_settings().aragon_max_prompt_bytes
    worst = _fixed_bytes() + C.INLINE_INDEX_MAX_BYTES + BASE_DIGEST_MAX
    headroom = limit - worst
    assert headroom >= REQUIRED_HEADROOM, (
        f"最坏情形余量 {headroom} B < {REQUIRED_HEADROOM} B"
        f"（schema+system+task={_fixed_bytes()}，索引上限={C.INLINE_INDEX_MAX_BYTES}，base_digest={BASE_DIGEST_MAX}）"
    )


def test_the_longest_legal_index_fits_the_inline_cap() -> None:
    steps = [step(f"s{i}", "汉" * C.TITLE_MAX, sections=ALL_SECTIONS) for i in range(C.MAX_STEPS)]
    compiled = compile_workflow(chain_doc(*steps), sample_context(), template_name="汉" * C.NAME_MAX, version=999)
    assert len(compiled.inline_index.encode("utf-8")) <= C.INLINE_INDEX_MAX_BYTES


def test_plan_text_never_counts_against_the_prompt_budget() -> None:
    """The plan lives in `workflow.md`; only the signpost is inlined, whatever the plan weighs."""
    steps = [step(f"s{i}", sections=ALL_SECTIONS, prompt="汉" * 1000) for i in range(12)]
    compiled = compile_workflow(chain_doc(*steps), sample_context(), template_name="长流程", version=1)
    assert len(compiled.plan_md.encode("utf-8")) > 3 * C.INLINE_INDEX_MAX_BYTES
    assert len(compiled.inline_index.encode("utf-8")) <= C.INLINE_INDEX_MAX_BYTES


def test_builtin_template_is_a_small_plan_and_a_small_index() -> None:
    builtin = __import__("app.services.workflow_builtin", fromlist=["builtin_doc"])
    compiled = compile_workflow(builtin.builtin_doc(), sample_context(), template_name=C.BUILTIN_NAME, version=1)
    assert len(compiled.plan_md.encode("utf-8")) < C.WARN_PLAN_BYTES
    assert len(compiled.inline_index.encode("utf-8")) < 1024
    limit = get_settings().aragon_max_prompt_bytes
    total = _fixed_bytes() + len(compiled.inline_index.encode("utf-8")) + BASE_DIGEST_MAX
    assert limit - total >= REQUIRED_HEADROOM
