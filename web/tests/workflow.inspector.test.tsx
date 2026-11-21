import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Inspector } from '@/components/workflow/Inspector';
import { parseCeiling } from '@/components/workflow/PolicyFields';
import { scanPlaceholders, undefinedPlaceholders } from '@/lib/workflow/canvasModel';
import { useWorkflowStore } from '@/lib/workflow/store';

import { makeWorkflowsApi, sampleDetail, sampleMeta } from './workflowHarness';

const workflowsApi = vi.hoisted(() => ({}) as ReturnType<typeof makeWorkflowsApi>);
vi.mock('@/lib/api.admin', () => ({ adminApi: { workflows: workflowsApi } }));
const push = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

function select(nodes: string[], edges: string[] = []) {
  act(() => useWorkflowStore.getState().setSelection({ nodes, edges }));
}

const nodeOf = (id: string) => useWorkflowStore.getState().doc?.nodes.find((node) => node.id === id);

beforeEach(() => {
  Object.assign(workflowsApi, makeWorkflowsApi());
  push.mockReset();
  useWorkflowStore.getState().reset();
  useWorkflowStore.getState().load(sampleDetail());
});

afterEach(() => {
  vi.useRealTimers();
  useWorkflowStore.getState().reset();
});

describe('占位符的词法扫描', () => {
  it('flags_undefined_placeholders_and_ignores_escaped_ones', () => {
    const text = '读 {{instrument.symbol}}，别用 {{nope}}，字面量 \\{{literal}}';
    expect(undefinedPlaceholders(text)).toEqual(['nope']);
    const tokens = scanPlaceholders(text);
    expect(tokens.map((token) => [token.name, token.defined, token.escaped])).toEqual([
      ['instrument.symbol', true, false],
      ['nope', false, false],
      ['literal', false, true],
    ]);
  });

  it('parses_policy_ceilings_strictly', () => {
    expect(parseCeiling('', 40)).toBeNull();
    expect(parseCeiling('20', 40)).toBe(20);
    expect(parseCeiling('41', 40)).toBeUndefined();
    expect(parseCeiling('0', 40)).toBeUndefined();
    expect(parseCeiling('1.5', 40)).toBeUndefined();
    expect(parseCeiling('abc', 40)).toBeUndefined();
  });
});

describe('Inspector —— 未选中：流程策略与模板信息', () => {
  it('shows_policy_ceiling_hint_and_template_name_warning', () => {
    render(<Inspector />);
    expect(screen.getByText(/全站上限 40 回合/)).toBeInTheDocument();
    expect(screen.getByText(/名称会显示在用户的研报元信息中/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('估值加强版')).toBeInTheDocument();
  });

  it('tightens_max_turns_through_a_set_policy_op_on_blur', async () => {
    render(<Inspector />);
    const input = screen.getByLabelText('最大回合');
    fireEvent.change(input, { target: { value: '20' } });
    fireEvent.blur(input);
    expect(useWorkflowStore.getState().doc?.policy.maxTurns).toBe(20);
  });

  it('does_not_commit_a_turn_limit_above_the_site_ceiling', () => {
    render(<Inspector />);
    const input = screen.getByLabelText('最大回合');
    fireEvent.change(input, { target: { value: '99' } });
    fireEvent.blur(input);
    expect(useWorkflowStore.getState().doc?.policy.maxTurns).toBeNull();
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('Inspector —— 步骤', () => {
  it('commits_a_prompt_edit_after_the_600ms_pause_as_one_update_node', () => {
    vi.useFakeTimers();
    select(['n_s1']);
    render(<Inspector />);
    const area = screen.getByLabelText(/提示词/);
    fireEvent.change(area, { target: { value: '新的提示词' } });
    expect(nodeOf('n_s1')?.config.prompt).toBe('读取 {{instrument.symbol}} 的资料');
    act(() => {
      vi.advanceTimersByTime(650);
    });
    expect(nodeOf('n_s1')?.config.prompt).toBe('新的提示词');
    expect(nodeOf('n_s1')?.rev).toBe(2);
  });

  it('commits_immediately_on_blur_without_waiting_for_the_pause', () => {
    select(['n_s1']);
    render(<Inspector />);
    const title = screen.getByLabelText('标题');
    fireEvent.change(title, { target: { value: '更名' } });
    fireEvent.blur(title);
    expect(nodeOf('n_s1')?.title).toBe('更名');
  });

  it('marks_an_undefined_placeholder_in_red_with_text_not_only_colour', () => {
    select(['n_s1']);
    render(<Inspector />);
    fireEvent.change(screen.getByLabelText(/提示词/), { target: { value: '用 {{nope}}' } });
    const flagged = screen.getByText(/未定义的占位符 \{\{nope\}\}/);
    expect(flagged).toBeInTheDocument();
    expect(screen.getByLabelText(/提示词/)).toHaveAttribute('aria-invalid', 'true');
  });

  it('toggles_a_reference_file_and_a_section_with_one_op_each', async () => {
    select(['n_s2']);
    render(<Inspector />);
    await userEvent.click(screen.getByRole('checkbox', { name: /bars\.csv/ }));
    expect(nodeOf('n_s2')?.config.files).toEqual(['bars.csv']);
    await userEvent.click(screen.getByRole('checkbox', { name: /风险/ }));
    expect(nodeOf('n_s2')?.config.sections).toEqual(['valuation', 'risks']);
  });

  it('shows_section_coverage_status_by_text', () => {
    select(['n_s2']);
    render(<Inspector />);
    expect(screen.getAllByText(/已覆盖/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/未被任何步骤覆盖/).length).toBeGreaterThan(0);
  });

  it('changes_role_and_effort', async () => {
    select(['n_s1']);
    render(<Inspector />);
    fireEvent.change(screen.getByLabelText('角色'), { target: { value: 'review' } });
    expect(nodeOf('n_s1')?.config.role).toBe('review');
    await userEvent.click(screen.getByRole('radio', { name: '深' }));
    expect(nodeOf('n_s1')?.config.effort).toBe('deep');
  });

  it('adds_an_upstream_link_through_the_picker_and_blocks_cycles', async () => {
    select(['n_s3']);
    render(<Inspector />);
    await userEvent.click(screen.getByTestId('wf-picker-upstream'));
    const options = screen.getAllByRole('option');
    const s2 = options.find((option) => option.textContent?.includes('S2'));
    expect(s2).toBeDefined();
    await userEvent.click(s2!.querySelector('input')!);
    expect(useWorkflowStore.getState().doc?.edges.some((edge) => edge.source === 'n_s2' && edge.target === 'n_s3')).toBe(true);
    const gate = options.find((option) => option.textContent?.includes('G1'));
    expect(gate).toHaveAttribute('aria-disabled', 'true');
  });

  it('deletes_the_node_from_the_inspector_button', async () => {
    select(['n_s3']);
    render(<Inspector />);
    await userEvent.click(screen.getByRole('button', { name: /删除节点/ }));
    expect(nodeOf('n_s3')).toBeUndefined();
  });
});

describe('Inspector —— 检查点 / 注释 / 连线', () => {
  it('edits_the_gate_checklist', () => {
    select(['n_g1']);
    render(<Inspector />);
    const box = screen.getByLabelText('检查清单');
    fireEvent.change(box, { target: { value: '币种、单位一致' } });
    fireEvent.blur(box);
    expect(nodeOf('n_g1')?.config.checklist).toBe('币种、单位一致');
  });

  it('clears_an_optional_key_with_null_when_the_text_is_emptied', () => {
    select(['n_g1']);
    render(<Inspector />);
    const box = screen.getByLabelText('检查清单');
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.blur(box);
    expect(nodeOf('n_g1')?.config.checklist).toBeUndefined();
  });

  it('labels_and_deletes_an_edge', async () => {
    select([], ['e_2']);
    render(<Inspector />);
    const label = screen.getByLabelText('连线标签');
    fireEvent.change(label, { target: { value: '并行' } });
    fireEvent.blur(label);
    expect(useWorkflowStore.getState().doc?.edges.find((edge) => edge.id === 'e_2')?.label).toBe('并行');
    await userEvent.click(screen.getByRole('button', { name: /删除连线/ }));
    expect(useWorkflowStore.getState().doc?.edges.some((edge) => edge.id === 'e_2')).toBe(false);
  });

  it('shows_a_count_for_multi_selection_and_no_form', () => {
    select(['n_s1', 'n_s2']);
    render(<Inspector />);
    expect(screen.getByText('已选中 2 个节点')).toBeInTheDocument();
    expect(screen.queryByLabelText('标题')).not.toBeInTheDocument();
  });
});

describe('Inspector —— 内置模板只读（AC-W31）', () => {
  beforeEach(() => {
    useWorkflowStore.getState().load(sampleDetail({ meta: { isBuiltin: true, name: '标准五段式研究' } }));
  });

  it('disables_every_control_and_shows_the_banner', () => {
    select(['n_s1']);
    render(<Inspector />);
    expect(screen.getByText(/内置流程只读/)).toBeInTheDocument();
    expect(screen.getByLabelText('标题')).toBeDisabled();
    expect(screen.getByLabelText(/提示词/)).toBeDisabled();
    expect(screen.getByLabelText('角色')).toBeDisabled();
    expect(screen.getByRole('button', { name: /删除节点/ })).toBeDisabled();
    expect(screen.getByTestId('wf-picker-upstream')).toBeDisabled();
  });

  it('duplicates_into_an_editable_copy_and_navigates_to_it', async () => {
    workflowsApi.create.mockResolvedValue({ meta: sampleMeta({ id: 'wf_copy' }) });
    render(<Inspector />);
    await userEvent.click(screen.getByRole('button', { name: '复制为可编辑副本' }));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/admin/workflows/wf_copy'));
    expect(workflowsApi.create).toHaveBeenCalledWith({ name: '标准五段式研究 副本', from: { templateId: 'wf_1' } });
  });

  it('shows_an_error_when_duplicating_fails', async () => {
    workflowsApi.create.mockRejectedValue(new Error('boom'));
    render(<Inspector />);
    await userEvent.click(screen.getByRole('button', { name: '复制为可编辑副本' }));
    expect(await screen.findByText('复制失败，请稍后重试')).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
