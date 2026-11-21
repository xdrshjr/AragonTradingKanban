import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BottomDock } from '@/components/workflow/BottomDock';
import { ProblemsPanel, ProblemsView } from '@/components/workflow/ProblemsPanel';
import { groupPhases, StructurePane } from '@/components/workflow/StructurePane';
import { issuesByEntity } from '@/lib/workflow/canvasModel';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { ValidationIssue, ValidationReport } from '@/lib/workflow/types';

import { emptyReport, makeWorkflowsApi, sampleDetail, sampleDoc } from './workflowHarness';

const workflowsApi = vi.hoisted(() => ({}) as ReturnType<typeof makeWorkflowsApi>);
vi.mock('@/lib/api.admin', () => ({ adminApi: { workflows: workflowsApi } }));

const ISSUES: ValidationIssue[] = [
  { code: 'missing_success_criteria', severity: 'warning', entityKind: 'node', entityId: 'n_s2' },
  { code: 'empty_prompt', severity: 'error', entityKind: 'node', entityId: 'n_s3' },
  { code: 'section_not_covered', severity: 'error', entityKind: 'graph', entityId: null, params: { section: 'risks' } },
];

function report(): ValidationReport {
  return { issues: ISSUES, errorCount: 2, warningCount: 1, publishable: false, blank: false };
}

beforeEach(() => {
  Object.assign(workflowsApi, makeWorkflowsApi());
  useWorkflowStore.getState().reset();
  useWorkflowStore.getState().load(sampleDetail({ validation: report() }));
});

afterEach(() => {
  useWorkflowStore.getState().reset();
});

describe('issuesByEntity', () => {
  it('counts_errors_and_warnings_per_node_and_skips_graph_level_issues', () => {
    const counts = issuesByEntity(report());
    expect(counts.n_s3).toEqual({ errors: 1, warnings: 0 });
    expect(counts.n_s2).toEqual({ errors: 0, warnings: 1 });
    expect(Object.keys(counts)).toHaveLength(2);
  });
});

describe('ProblemsView（AC-W29）', () => {
  it('lists_errors_before_warnings_with_server_codes_as_chinese_sentences', () => {
    render(<ProblemsView report={report()} doc={sampleDoc()} onLocate={() => undefined} />);
    const rows = screen.getAllByTestId('wf-problem');
    expect(rows.map((row) => row.getAttribute('data-severity'))).toEqual(['error', 'error', 'warning']);
    expect(screen.getByText(/「风险」的提示词为空/)).toBeInTheDocument();
    expect(screen.getByText(/章节「风险」没有任何步骤服务/)).toBeInTheDocument();
  });

  it('prefixes_severity_in_text_for_screen_readers', () => {
    render(<ProblemsView report={report()} doc={sampleDoc()} onLocate={() => undefined} />);
    expect(screen.getAllByText(/^错误：/).length).toBe(2);
    expect(screen.getByText(/^警告：/)).toBeInTheDocument();
  });

  it('locates_a_node_issue_but_not_a_graph_level_one', async () => {
    const onLocate = vi.fn();
    render(<ProblemsView report={report()} doc={sampleDoc()} onLocate={onLocate} />);
    const rows = screen.getAllByTestId('wf-problem');
    expect(rows[1]).toBeDisabled();
    await userEvent.click(rows[0]!);
    expect(onLocate).toHaveBeenCalledWith(expect.objectContaining({ entityId: 'n_s3' }));
  });

  it('says_so_when_the_graph_is_blank_and_when_there_is_nothing_to_report', () => {
    const { rerender } = render(<ProblemsView report={emptyReport({ blank: true })} doc={sampleDoc()} onLocate={() => undefined} />);
    expect(screen.getByText(/空白流程/)).toBeInTheDocument();
    rerender(<ProblemsView report={emptyReport()} doc={sampleDoc()} onLocate={() => undefined} />);
    expect(screen.getByText('没有发现问题。')).toBeInTheDocument();
  });

  it('selects_and_highlights_the_node_when_a_store_connected_row_is_clicked', async () => {
    render(<ProblemsPanel />);
    await userEvent.click(screen.getAllByTestId('wf-problem')[0]!);
    expect(useWorkflowStore.getState().selection.nodes).toEqual(['n_s3']);
    expect(useWorkflowStore.getState().highlightIds).toEqual(['n_s3']);
  });
});

describe('BottomDock', () => {
  it('starts_collapsed_as_a_title_bar_with_error_and_warning_counts_always_visible', () => {
    render(<BottomDock />);
    expect(screen.getByRole('tab', { name: /问题/ })).toHaveTextContent('2');
    expect(screen.getByRole('tab', { name: /问题/ })).toHaveTextContent('1');
    expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument();
    expect(screen.getByTestId('wf-dock-toggle')).toHaveAttribute('aria-expanded', 'false');
  });

  it('expands_to_the_clicked_tab_and_collapses_again', async () => {
    render(<BottomDock />);
    await userEvent.click(screen.getByTestId('wf-dock-tab-problems'));
    expect(screen.getByRole('tabpanel')).toBeInTheDocument();
    expect(screen.getAllByTestId('wf-problem')).toHaveLength(3);
    await userEvent.click(screen.getByTestId('wf-dock-toggle'));
    expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument();
  });

  it('expands_when_the_status_bar_asks_for_the_problems_tab', () => {
    render(<BottomDock />);
    act(() => useWorkflowStore.getState().setDockTab('problems'));
    expect(screen.getByRole('tabpanel')).toBeInTheDocument();
  });

  it('renders_the_compiled_plan_in_the_preview_tab', async () => {
    workflowsApi.preview.mockResolvedValue({
      planMd: '# 研究流程：估值加强版',
      inlineIndex: '',
      levels: [],
      bytes: 2048,
      sha1: 'abc123',
      budget: { promptTotal: 24000, limit: 32768 },
    });
    render(<BottomDock />);
    await userEvent.click(screen.getByTestId('wf-dock-tab-plan'));
    expect(await screen.findByTestId('wf-plan-md')).toHaveTextContent('研究流程：估值加强版');
    expect(screen.getByText(/sha1 abc123/)).toBeInTheDocument();
  });

  it('renders_usage_rows_from_the_usage_endpoint', async () => {
    workflowsApi.usage.mockResolvedValue({
      byVersion: [{ version: 2, runs: 7, succeeded: 6, failed: 1, adherence: { full: 4, partial: 2, none: 1 }, fallbacks: 1 }],
    });
    render(<BottomDock />);
    await userEvent.click(screen.getByTestId('wf-dock-tab-usage'));
    expect(await screen.findByText('v2')).toBeInTheDocument();
    expect(screen.getByText(/完整 4 · 部分 2 · 无 1/)).toBeInTheDocument();
  });
});

describe('StructurePane', () => {
  it('groups_steps_and_gates_into_phases_and_lists_notes_separately', () => {
    const groups = groupPhases(sampleDoc());
    expect(groups.phases.map((phase) => phase.nodes.map((entry) => entry.key))).toEqual([['S1'], ['S2', 'S3'], ['G1']]);
    expect(groups.notes).toHaveLength(1);
    expect(groups.stray).toHaveLength(0);
  });

  it('flags_nodes_that_are_not_connected_to_the_flow', () => {
    const doc = sampleDoc();
    doc.edges = doc.edges.filter((edge) => edge.id !== 'e_1');
    const groups = groupPhases(doc);
    expect(groups.stray.map((node) => node.id)).toContain('n_s1');
  });

  it('selects_a_node_in_the_canvas_when_its_row_is_clicked', async () => {
    render(<StructurePane />);
    await userEvent.click(screen.getByTestId('wf-structure-n_s2'));
    expect(useWorkflowStore.getState().selection.nodes).toEqual(['n_s2']);
  });
});
