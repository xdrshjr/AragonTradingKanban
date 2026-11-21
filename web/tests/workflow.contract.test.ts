import { describe, expect, it } from 'vitest';

import permissions from '../../contracts/permissions.json';
import { permissionsForRole } from '@/lib/permissions';
import {
  BUILTIN_TEMPLATE_ID,
  EFFORTS,
  FILES,
  ID_RE,
  LIMITS,
  NODE_KINDS,
  PLACEHOLDERS,
  RESERVED_IDS,
  ROLES,
  SECTIONS,
  WORKFLOW_CONTRACT,
} from '@/lib/workflow/contract';
import { copyWorkflow, describeIssue } from '@/lib/workflow/copy.workflow';

describe('contracts/workflow.json 的前端读取', () => {
  it('限额与契约逐项一致', () => {
    expect(LIMITS.MAX_NODES).toBe(60);
    expect(LIMITS.MAX_STEPS).toBe(30);
    expect(LIMITS.PROMPT_MAX).toBe(2000);
    expect(LIMITS.TITLE_MAX).toBe(40);
    expect(LIMITS.ZOOM_MIN).toBe(0.2);
    expect(Object.keys(WORKFLOW_CONTRACT.limits).length).toBeGreaterThan(20);
  });

  it('枚举、章节、资料文件、占位符与契约一致', () => {
    expect(NODE_KINDS).toEqual(['start', 'step', 'gate', 'note', 'end']);
    expect(ROLES).toEqual(['data', 'analysis', 'synthesis', 'review', 'custom']);
    expect(EFFORTS).toEqual(['light', 'normal', 'deep']);
    expect(SECTIONS.map((s) => s.id)).toEqual(['thesis', 'fundamentals', 'valuation', 'risks', 'catalysts']);
    expect(FILES.map((f) => f.name)).toContain('bars.csv');
    expect(PLACEHOLDERS).toContain('instrument.symbol');
    expect(BUILTIN_TEMPLATE_ID).toBe('wf_builtin_standard');
  });

  it('id 规则：保留字与字符集', () => {
    expect(RESERVED_IDS.has('__proto__')).toBe(true);
    expect(ID_RE.test('n_ab12cd34')).toBe(true);
    expect(ID_RE.test('bad id!')).toBe(false);
  });

  it('每个错误/警告码都有中文句子（未知码才原样显示）', () => {
    const codes = [...WORKFLOW_CONTRACT.issueCodes.errors, ...WORKFLOW_CONTRACT.issueCodes.warnings];
    expect(codes.length).toBeGreaterThan(30);
    for (const code of codes) {
      const sentence = describeIssue({ code, severity: 'error', entityKind: 'graph', entityId: null }, () => '步骤');
      expect(sentence, code).not.toBe(code);
    }
    const unknown = describeIssue({ code: 'brand_new', severity: 'error', entityKind: 'graph', entityId: null });
    expect(unknown).toBe('brand_new');
  });

  it('所有章节都有展示文案', () => {
    for (const section of SECTIONS) expect(copyWorkflow.sections[section.id]).toBeTruthy();
  });
});

describe('权限词表（admin.workflow.manage）', () => {
  it('admin 与 root 拥有，user / guest / service 没有', () => {
    expect(permissionsForRole('admin')).toContain('admin.workflow.manage');
    expect(permissionsForRole('root')).toContain('admin.workflow.manage');
    expect(permissionsForRole('user')).not.toContain('admin.workflow.manage');
    expect(permissionsForRole('guest')).not.toContain('admin.workflow.manage');
    expect(permissionsForRole('service')).not.toContain('admin.workflow.manage');
    expect(permissions.service.minus).toContain('admin.workflow.manage');
  });
});
