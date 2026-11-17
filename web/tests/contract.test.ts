import { describe, expect, it } from 'vitest';

import { BLOCK_TYPES } from '@/lib/types';
import type {
  BudgetState,
  Comparability,
  CostEntry,
  CostSummary,
  EngineCapability,
  EngineInfo,
  GroundingQuality,
  GroundingSample,
  BlockRangeResponse,
} from '@/lib/types';
import backendSchema from '../../api/app/agent/schemas/report_doc.schema.json';

/**
 * Contract guard (spec §4): `web/lib/types.ts` mirrors `api/app/schemas/*` by
 * hand, so this test fails the moment the two drift on what actually crosses the
 * wire. It reads the *backend* JSON Schema directly — the same file the CLI is
 * handed — which makes the drift impossible to hide.
 *
 * ★v6 §19.3.1 规则 4 扩表：三个新类型（`GroundingQuality` / `Comparability` /
 * `CostSummary`）必须与 Pydantic 模型**逐字段同名同可选性**。这里是逐字抄来的
 * 字段清单，抄错就红 —— 它是唯一能挡住"后端加了字段、前端永远看不到"的东西。
 */

interface JsonSchemaNode {
  $defs?: Record<string, JsonSchemaNode>;
  anyOf?: JsonSchemaNode[];
  properties?: Record<string, JsonSchemaNode>;
  required?: string[];
  enum?: unknown[];
}

const schema = backendSchema as unknown as JsonSchemaNode;

describe('report document contract', () => {
  it('declares the same twelve block types the front end renders', () => {
    const variants = schema.$defs?.block?.anyOf ?? [];
    const declared = variants
      .map((variant) => variant.properties?.type?.enum?.[0])
      .filter((value): value is string => typeof value === 'string')
      .sort();
    expect(declared).toEqual([...BLOCK_TYPES].sort());
  });

  it('requires the same top-level fields the TS type declares', () => {
    expect([...(schema.required ?? [])].sort()).toEqual(
      [
        'asOf',
        'disclaimer',
        'headline',
        'instrument',
        'keyMetrics',
        'rating',
        'risks',
        'schema',
        'sections',
        'sources',
        'summary',
      ].sort(),
    );
  });

  it('keeps the schema id stable', () => {
    // The document pins its version with `const`, not `enum`.
    const node = schema.properties?.schema as unknown as { const?: string; enum?: string[] };
    expect(node?.const ?? node?.enum?.[0]).toBe('aragon.research.report/v1');
  });

  it('allows a provisional flag so a failed run can still be rendered', () => {
    expect(schema.properties?.provisional).toBeDefined();
  });

  it('forbids unknown top-level fields, matching the TS strictness', () => {
    const root = backendSchema as unknown as { additionalProperties?: boolean };
    expect(root.additionalProperties).toBe(false);
  });
});

/**
 * ★v6 镜像：字段清单与 `api/app/schemas/{report,cost,blocks,capabilities}.py`
 * 逐字对齐。用 `satisfies` + `keyof` 双重断言：少一个字段编译期就红，多一个运行期
 * 也不通过 —— 两侧都是"必须一模一样"。
 */
describe('v6 mirror fields', () => {
  it('GroundingQuality mirrors numeric_claims.GroundingReport.as_payload()', () => {
    const shape: Record<keyof GroundingQuality, true> = {
      factClaims: true,
      factMissing: true,
      totalMissing: true,
      // ★A4：按值去重（引用次数 vs 数值个数）
      distinctMissing: true,
      distinctClaims: true,
      // ★R3-1：块级 / 值级两个分母（samples 按 blockId 去重）
      sampledBlocks: true,
      sampledValues: true,
      projectionClaims: true,
      derivedClaims: true,
      identifierClaims: true,
      skipped: true,
      samples: true,
      // ★A6：口径版本 + 输入指纹
      rules: true,
      docHash: true,
      snapshotHash: true,
    };
    expect(Object.keys(shape).sort()).toEqual(
      [
        'derivedClaims',
        'distinctClaims',
        'distinctMissing',
        'docHash',
        'factClaims',
        'factMissing',
        'identifierClaims',
        'projectionClaims',
        'rules',
        'sampledBlocks',
        'sampledValues',
        'samples',
        'skipped',
        'snapshotHash',
        'totalMissing',
      ].sort(),
    );
  });

  it('GroundingSample carries the four coordinates the footnote needs', () => {
    const sample: GroundingSample = { blockId: 'valuation#2', path: 'valuation.table[1].rows[0].pe', raw: '28.4', text: '…' };
    expect(Object.keys(sample).sort()).toEqual(['blockId', 'path', 'raw', 'text']);
  });

  it('EngineInfo is exactly four fields — no model, no provider (C-18)', () => {
    const engine: EngineInfo = { fingerprint: 'abc', cliVersion: '0.6.68', allowTools: [], schemaBytes: 1 };
    expect(Object.keys(engine).sort()).toEqual(['allowTools', 'cliVersion', 'fingerprint', 'schemaBytes']);
    expect('model' in engine).toBe(false);
    expect('provider' in engine).toBe(false);
  });

  it('EngineCapability mirrors the new top-level /capabilities.engine object', () => {
    const capability: EngineCapability = { fingerprint: null, drift: false, schemaBytes: 0, allowTools: [] };
    expect(Object.keys(capability).sort()).toEqual(['allowTools', 'drift', 'fingerprint', 'schemaBytes']);
  });

  it('Comparability keeps `same` tri-state and the explicit promise', () => {
    const variants: Comparability[] = [
      { same: true, changed: [], deltaUnaffected: true, note: '' },
      { same: false, changed: ['model'], deltaUnaffected: true, note: '两版由不同引擎生成' },
      { same: null, changed: [], deltaUnaffected: true, note: '其中一版没有引擎指纹（早期报告）' },
    ];
    expect(variants.map((item) => item.same)).toEqual([true, false, null]);
    expect(variants.every((item) => item.deltaUnaffected)).toBe(true);
  });

  it('budget.state has four members, not three (★V6-P2-5)', () => {
    const states: BudgetState[] = ['ok', 'near', 'exceeded', 'unlimited'];
    expect(states).toHaveLength(4);
    expect(new Set(states).size).toBe(4);
  });

  it('CostSummary allows a null remainingUsd only through the budget object', () => {
    const summary: CostSummary = {
      window: '30d',
      from: '2026-01-11T00:00:00Z',
      to: '2026-02-10T00:00:00Z',
      spentUsd: 3.42,
      // ★v7：`spentUsd`（只含回执）/ `estimatedUsd`（只含估算）/ 两者的合计。
      estimatedUsd: 0.31,
      spentIncludingEstimatedUsd: 3.73,
      knownRuns: 18,
      reportedRuns: 18,
      estimatedRuns: 3,
      unknownRuns: 4,
      byKind: { run: 2.91, ask_deep: 0.51 },
      byModel: [{ model: 'claude-sonnet', usd: 3.42, runs: 18 }],
      budget: { monthlyUsd: 0, remainingUsd: null, resetAt: null, state: 'unlimited' },
      priceVersion: '2026-03',
      ledgerGap: 0,
    };
    expect(summary.unknownRuns).toBe(4);
    expect(summary.budget.remainingUsd).toBeNull();
    // ★v7（AC-79）：三态各计一次，且"含估算"= 两者相加（不是重复计）。
    expect(summary.reportedRuns + summary.estimatedRuns + summary.unknownRuns).toBe(25);
    expect(summary.spentIncludingEstimatedUsd).toBeCloseTo(summary.spentUsd + summary.estimatedUsd);
  });

  it('EngineCapability mirrors the v7 additions without model/provider (H-19)', () => {
    const capability: EngineCapability = {
      fingerprint: 'abc123abc123',
      drift: false,
      schemaBytes: 18392,
      allowTools: ['read_file'],
      posture: 'lean',
      schemaSha1: '7a93509904b4',
      schema: { bytes: 18392, depth: 11, nodes: 658, maxBytes: 65536, maxDepth: 12, maxNodes: 5000, headroom: 1, ok: true },
      limits: { retryMax: 6, repairTurns: 2, maxOutputTokens: 40000, valueBytes: 262144, schemaBytes: 65536 },
      priceVersion: '2026-03',
      lastFailure: null,
    };
    expect(Object.keys(capability).sort()).toEqual(
      [
        'allowTools',
        'drift',
        'fingerprint',
        'lastFailure',
        'limits',
        'posture',
        'priceVersion',
        'schema',
        'schemaBytes',
        'schemaSha1',
      ].sort(),
    );
    expect('model' in capability).toBe(false);
    expect(capability.schema?.headroom).toBe(1);
  });

  it('CostEntry carries the tri-state source next to the two amounts (AC-86)', () => {
    const entry: CostEntry = {
      id: 1,
      kind: 'run',
      refId: 'run-1',
      symbol: '600519.SH',
      reportId: 'rep-1',
      model: 'deepseek-flash',
      usd: null,
      known: false,
      usdEst: 0.0172,
      source: 'estimated',
      createdAt: '2026-02-10T00:00:00Z',
    };
    // `estimated` 行的 `usd` 必须为空：两列互斥，不是"两个数都填"。
    expect(entry.usd).toBeNull();
    expect(entry.usdEst).toBeCloseTo(0.0172);
    expect(entry.source).toBe('estimated');
  });

  it('BlockRangeResponse mirrors the /reports/{id}/blocks envelope', () => {
    const response: BlockRangeResponse = {
      reportId: 'r1',
      range: { from: '2025-03-01', to: '2025-04-15' },
      items: [
        {
          blockId: 'valuation#2',
          sectionId: 'valuation',
          sectionTitle: '估值',
          kind: 'table',
          overlapDays: 46,
          matchKind: 'quarter',
          text: '…',
        },
      ],
      total: 1,
      truncated: false,
      asOf: '2026-02-10T01:00:00Z',
      reason: null,
    };
    expect(Object.keys(response).sort()).toEqual(
      ['asOf', 'items', 'range', 'reason', 'reportId', 'total', 'truncated'].sort(),
    );
    expect(Object.keys(response.items[0]!).sort()).toEqual(
      ['blockId', 'kind', 'matchKind', 'overlapDays', 'sectionId', 'sectionTitle', 'text'].sort(),
    );
  });
});
