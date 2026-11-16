import { describe, expect, it } from 'vitest';

import { BLOCK_TYPES } from '@/lib/types';
import backendSchema from '../../api/app/agent/schemas/report_doc.schema.json';

/**
 * Contract guard (spec §4): `web/lib/types.ts` mirrors `api/app/schemas/*` by
 * hand, so this test fails the moment the two drift on what actually crosses the
 * wire. It reads the *backend* JSON Schema directly — the same file the CLI is
 * handed — which makes the drift impossible to hide.
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
