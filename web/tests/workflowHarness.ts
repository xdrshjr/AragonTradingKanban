/**
 * 画布 / 检查器 / 问题 / 发布 四组测试共用的夹具（FB）。
 * 不含 `.test` 后缀，vitest 不会把它当用例收集。
 */

import { vi } from 'vitest';

import type { TemplateDetail, TemplateSummary, ValidationReport, WorkflowDoc, WorkflowNode } from '@/lib/workflow/types';

/** 6 个节点的小流程：start → S1 ─┬→ S2 ─┐ → G1 → end ；S3 并行。 */
export function sampleDoc(): WorkflowDoc {
  const node = (id: string, kind: WorkflowNode['kind'], title: string, x: number, y: number, config: WorkflowNode['config'] = {}): WorkflowNode => ({
    id,
    kind,
    title,
    position: { x, y },
    rev: 1,
    config,
  });
  return {
    schemaVersion: 1,
    nodes: [
      node('n_start', 'start', '开始', 0, 100),
      node('n_s1', 'step', '核对事实底座', 300, 100, {
        role: 'data',
        prompt: '读取 {{instrument.symbol}} 的资料',
        files: ['snapshot.json'],
        sections: ['thesis'],
        effort: 'normal',
      }),
      node('n_s2', 'step', '技术面', 600, 40, { role: 'analysis', prompt: '分析', sections: ['valuation'] }),
      node('n_s3', 'step', '风险', 600, 200, { role: 'review', prompt: '复核风险' }),
      node('n_g1', 'gate', '估值前核对', 900, 100, { checklist: '口径一致？' }),
      node('n_end', 'end', '提交', 1200, 100),
      node('n_note', 'note', '说明', 300, 320, { body: '港股注意币种' }),
    ],
    edges: [
      { id: 'e_1', source: 'n_start', target: 'n_s1', label: '', rev: 1 },
      { id: 'e_2', source: 'n_s1', target: 'n_s2', label: '', rev: 1 },
      { id: 'e_3', source: 'n_s1', target: 'n_s3', label: '', rev: 1 },
      { id: 'e_4', source: 'n_s2', target: 'n_g1', label: '', rev: 1 },
      { id: 'e_5', source: 'n_s3', target: 'n_g1', label: '', rev: 1 },
      { id: 'e_6', source: 'n_g1', target: 'n_end', label: '', rev: 1 },
    ],
    policy: { maxTurns: null, maxDurationMinutes: null, progressMarkers: true },
    viewport: { x: 10, y: 10, zoom: 1 },
  };
}

export function emptyReport(overrides: Partial<ValidationReport> = {}): ValidationReport {
  return { issues: [], errorCount: 0, warningCount: 0, publishable: true, blank: false, ...overrides };
}

export function sampleMeta(overrides: Partial<TemplateSummary> = {}): TemplateSummary {
  return {
    id: 'wf_1',
    name: '估值加强版',
    description: '',
    isDefault: false,
    isBuiltin: false,
    status: 'active',
    latestVersion: null,
    stepCount: 3,
    dirty: true,
    updatedAt: '2026-01-01T00:00:00Z',
    updatedByName: null,
    runs30d: 0,
    ...overrides,
  };
}

export function sampleDetail(overrides: { meta?: Partial<TemplateSummary>; doc?: WorkflowDoc; validation?: ValidationReport } = {}): TemplateDetail {
  return {
    meta: sampleMeta(overrides.meta),
    draft: {
      doc: overrides.doc ?? sampleDoc(),
      revision: 5,
      validation: overrides.validation ?? emptyReport(),
      dirty: true,
    },
    versions: [],
  };
}

/** `adminApi.workflows` 的桩：每个方法都是 vi.fn，ops 默认成功。 */
export function makeWorkflowsApi() {
  return {
    ops: vi.fn(async (_id: string, baseRevision: number) => ({
      revision: baseRevision + 1,
      validation: emptyReport(),
      changed: true,
    })),
    get: vi.fn(),
    create: vi.fn(),
    patch: vi.fn(),
    preview: vi.fn(),
    publish: vi.fn(),
    version: vi.fn(),
    versions: vi.fn(),
    restore: vi.fn(),
    rollback: vi.fn(),
    usage: vi.fn(),
    validate: vi.fn(),
  };
}
