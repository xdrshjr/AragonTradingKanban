import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PublishDialog } from '@/components/workflow/PublishDialog';
import { VersionDiff } from '@/components/workflow/VersionDiff';
import { VersionsPanel } from '@/components/workflow/VersionsPanel';
import { ApiError } from '@/lib/api';
import { diffDocs, isEmptyDiff } from '@/lib/workflow/canvasModel';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { ValidationReport, VersionSummary } from '@/lib/workflow/types';

import { makeWorkflowsApi, sampleDetail, sampleDoc, sampleMeta } from './workflowHarness';

const workflowsApi = vi.hoisted(() => ({}) as ReturnType<typeof makeWorkflowsApi>);
vi.mock('@/lib/api.admin', () => ({ adminApi: { workflows: workflowsApi } }));
vi.mock('@/components/ui/Toast', () => ({
  notify: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn(), copied: vi.fn() },
  ToastProvider: () => null,
}));
// 版本页签的只读画布会拉起 xyflow（jsdom 下很慢）：这里只验证它被渲染。
vi.mock('@/components/workflow/VersionCanvas', () => ({ default: () => <div data-testid="wf-version-canvas" /> }));

function version(n: number, overrides: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id: `v${n}`,
    version: n,
    hash: `hash000${n}`,
    changeNote: `第 ${n} 版`,
    stepCount: 3,
    planBytes: 1000,
    publishedAt: '2026-01-02T03:04:05Z',
    publishedByName: '王',
    ...overrides,
  };
}

function load(options: { validation?: ValidationReport; versions?: VersionSummary[]; meta?: Parameters<typeof sampleMeta>[0]; dirty?: boolean } = {}) {
  const detail = sampleDetail({ meta: options.meta, validation: options.validation });
  detail.versions = options.versions ?? [];
  detail.draft.dirty = options.dirty ?? true;
  useWorkflowStore.getState().load(detail);
}

beforeEach(() => {
  Object.assign(workflowsApi, makeWorkflowsApi());
  workflowsApi.get.mockResolvedValue(sampleDetail());
  useWorkflowStore.getState().reset();
});

afterEach(() => {
  useWorkflowStore.getState().reset();
});

describe('diffDocs', () => {
  it('reports_added_removed_changed_nodes_and_edge_counts_ignoring_position_and_notes', () => {
    const before = sampleDoc();
    const after = sampleDoc();
    after.nodes = after.nodes.filter((node) => node.id !== 'n_s3');
    after.edges = after.edges.filter((edge) => edge.target !== 'n_s3' && edge.source !== 'n_s3');
    after.nodes.push({ id: 'n_new', kind: 'step', title: '同业对比', position: { x: 0, y: 0 }, rev: 1, config: {} });
    after.nodes.find((node) => node.id === 'n_s1')!.title = '改名';
    after.nodes.find((node) => node.id === 'n_s2')!.position = { x: 9, y: 9 };
    after.nodes.find((node) => node.id === 'n_note')!.title = '改注释';
    const diff = diffDocs(before, after);
    expect(diff.addedNodes.map((node) => node.id)).toEqual(['n_new']);
    expect(diff.removedNodes.map((node) => node.id)).toEqual(['n_s3']);
    expect(diff.changedNodes.map((node) => node.id)).toEqual(['n_s1']);
    expect(diff.removedEdges).toBe(2);
    expect(diff.addedEdges).toBe(0);
  });

  it('treats_everything_as_added_for_the_first_version', () => {
    const diff = diffDocs(null, sampleDoc());
    expect(diff.addedNodes).toHaveLength(6);
    expect(diff.addedEdges).toBe(6);
  });

  it('is_empty_for_identical_documents', () => {
    expect(isEmptyDiff(diffDocs(sampleDoc(), sampleDoc()))).toBe(true);
  });
});

describe('VersionDiff', () => {
  it('renders_group_headings_with_counts_and_a_summary', () => {
    render(
      <VersionDiff
        diff={{ addedNodes: [{ id: 'a', title: '同业对比' }], removedNodes: [], changedNodes: [{ id: 'b', title: '估值' }], addedEdges: 2, removedEdges: 0 }}
      />,
    );
    expect(screen.getByText('新增节点 · 1')).toBeInTheDocument();
    expect(screen.getByText('修改节点 · 1')).toBeInTheDocument();
    expect(screen.getByText('+1 −0 ~1')).toBeInTheDocument();
  });

  it('says_when_nothing_changed_structurally', () => {
    render(<VersionDiff diff={{ addedNodes: [], removedNodes: [], changedNodes: [], addedEdges: 0, removedEdges: 0 }} />);
    expect(screen.getByText(/没有结构变更/)).toBeInTheDocument();
  });
});

describe('PublishDialog（AC-W30）', () => {
  it('shows_the_diff_note_default_checkbox_and_the_unresolved_warnings', async () => {
    load({
      validation: { issues: [{ code: 'missing_success_criteria', severity: 'warning', entityKind: 'node', entityId: 'n_s2' }], errorCount: 0, warningCount: 1, publishable: true, blank: false },
    });
    render(<PublishDialog open onOpenChange={() => undefined} />);
    expect(await screen.findByTestId('wf-diff')).toBeInTheDocument();
    expect(screen.getByText(/首个版本/)).toBeInTheDocument();
    expect(screen.getByLabelText(/变更说明/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /发布后设为默认/ })).toBeInTheDocument();
    expect(screen.getByText(/「技术面」没有完成标准/)).toBeInTheDocument();
    expect(screen.getByTestId('wf-publish-submit')).toBeEnabled();
  });

  it('disables_publishing_and_explains_why_when_there_are_errors', () => {
    load({
      validation: { issues: [{ code: 'empty_prompt', severity: 'error', entityKind: 'node', entityId: 'n_s3' }], errorCount: 1, warningCount: 0, publishable: false, blank: false },
    });
    render(<PublishDialog open onOpenChange={() => undefined} />);
    expect(screen.getByTestId('wf-publish-submit')).toBeDisabled();
    expect(screen.getByRole('alert', { name: '存在错误，无法发布' })).toHaveTextContent('「风险」的提示词为空');
  });

  it('publishes_with_the_note_and_set_default_then_refreshes_the_detail', async () => {
    load();
    workflowsApi.publish.mockResolvedValue(version(1));
    const onOpenChange = vi.fn();
    render(<PublishDialog open onOpenChange={onOpenChange} />);
    await userEvent.type(screen.getByLabelText(/变更说明/), '加了同业对比');
    await userEvent.click(screen.getByRole('checkbox', { name: /发布后设为默认/ }));
    await userEvent.click(screen.getByTestId('wf-publish-submit'));
    await waitFor(() => expect(workflowsApi.publish).toHaveBeenCalled());
    expect(workflowsApi.publish).toHaveBeenCalledWith('wf_1', { baseRevision: 5, changeNote: '加了同业对比', setDefault: true });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(workflowsApi.get).toHaveBeenCalled();
  });

  it('offers_no_default_checkbox_when_the_template_already_is_the_default', () => {
    load({ meta: { isDefault: true } });
    render(<PublishDialog open onOpenChange={() => undefined} />);
    expect(screen.getByRole('checkbox', { name: /本模板已是默认/ })).toBeDisabled();
  });

  it('blocks_publishing_an_unchanged_draft', async () => {
    load({ versions: [version(1)], dirty: false });
    workflowsApi.version.mockResolvedValue({ summary: version(1), doc: sampleDoc(), diffVsPrevious: null });
    render(<PublishDialog open onOpenChange={() => undefined} />);
    expect(await screen.findByText(/草稿与最新版本相同/)).toBeInTheDocument();
    expect(screen.getByTestId('wf-publish-submit')).toBeDisabled();
  });

  it('realigns_and_explains_on_a_revision_conflict', async () => {
    load();
    workflowsApi.publish.mockRejectedValue(new ApiError(409, { error: { code: 'workflow_revision_conflict', message: 'x', requestId: 'r', detail: {} } }));
    render(<PublishDialog open onOpenChange={() => undefined} />);
    await userEvent.click(screen.getByTestId('wf-publish-submit'));
    expect(await screen.findByText('草稿已被他人修改，请刷新后重试')).toBeInTheDocument();
    await waitFor(() => expect(workflowsApi.get).toHaveBeenCalled());
  });
});

describe('VersionsPanel', () => {
  beforeEach(() => {
    load({ versions: [version(1), version(2)], dirty: false });
  });

  it('lists_versions_newest_first_and_marks_the_latest', () => {
    render(<VersionsPanel />);
    const rows = screen.getAllByTestId(/wf-version-\d/);
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual(['wf-version-2', 'wf-version-1']);
    expect(within(rows[0]!).getByText('最新')).toBeInTheDocument();
  });

  it('opens_a_read_only_view_with_the_diff_against_the_previous_version', async () => {
    workflowsApi.version.mockResolvedValue({
      summary: version(2),
      doc: sampleDoc(),
      diffVsPrevious: { addedNodes: [{ id: 'x', title: '同业对比' }], removedNodes: [], changedNodes: [], addedEdges: 1, removedEdges: 0 },
    });
    render(<VersionsPanel />);
    await userEvent.click(within(screen.getByTestId('wf-version-2')).getByRole('button', { name: '查看' }));
    expect(await screen.findByTestId('wf-version-canvas')).toBeInTheDocument();
    expect(await screen.findByText('新增节点 · 1')).toBeInTheDocument();
    expect(workflowsApi.version).toHaveBeenCalledWith('wf_1', 2);
  });

  it('compares_a_version_with_the_current_draft', async () => {
    const old = sampleDoc();
    old.nodes = old.nodes.filter((node) => node.id !== 'n_s3');
    old.edges = old.edges.filter((edge) => edge.target !== 'n_s3' && edge.source !== 'n_s3');
    workflowsApi.version.mockResolvedValue({ summary: version(1), doc: old, diffVsPrevious: null });
    render(<VersionsPanel />);
    await userEvent.click(within(screen.getByTestId('wf-version-1')).getByRole('button', { name: '与草稿对比' }));
    expect(await screen.findByText('新增节点 · 1')).toBeInTheDocument();
  });

  it('asks_for_confirmation_before_rolling_back_and_then_refreshes', async () => {
    workflowsApi.rollback.mockResolvedValue(version(3));
    render(<VersionsPanel />);
    await userEvent.click(within(screen.getByTestId('wf-version-1')).getByRole('button', { name: /回滚/ }));
    expect(screen.getByRole('dialog', { name: '回滚到此版本？' })).toBeInTheDocument();
    expect(workflowsApi.rollback).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '回滚（发布为新版本）' }));
    await waitFor(() => expect(workflowsApi.rollback).toHaveBeenCalledWith('wf_1', 1));
    await waitFor(() => expect(workflowsApi.get).toHaveBeenCalled());
  });

  it('restores_a_version_into_the_draft', async () => {
    workflowsApi.restore.mockResolvedValue({ revision: 9 });
    render(<VersionsPanel />);
    await userEvent.click(within(screen.getByTestId('wf-version-2')).getByRole('button', { name: '恢复到草稿' }));
    await userEvent.click(screen.getByRole('button', { name: '恢复到草稿' }));
    await waitFor(() => expect(workflowsApi.restore).toHaveBeenCalledWith('wf_1', 2));
  });

  it('disables_restore_and_rollback_on_a_read_only_template', () => {
    load({ versions: [version(1)], meta: { isBuiltin: true }, dirty: false });
    render(<VersionsPanel />);
    const row = screen.getByTestId('wf-version-1');
    expect(within(row).getByRole('button', { name: /回滚/ })).toBeDisabled();
    expect(within(row).getByRole('button', { name: '恢复到草稿' })).toBeDisabled();
    expect(within(row).getByRole('button', { name: '查看' })).toBeEnabled();
  });

  it('shows_an_empty_state_when_nothing_was_ever_published', () => {
    load({ versions: [] });
    render(<VersionsPanel />);
    expect(screen.getByText('还没有发布过版本。')).toBeInTheDocument();
  });
});
