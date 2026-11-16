import { describe, expect, it } from 'vitest';

import { generationReducer, initialGenerationState, type GenerationState } from '@/components/generation/useGenerationStream';
import { buildEventsUrl, retryDelay } from '@/lib/sse';
import type { RunStreamEvent } from '@/lib/sse';

/**
 * The generation reducer is pure, so it can be driven with a scripted event list.
 * This covers AC-4 (six stages advance) and AC-10/AC-11 (error and cancel states).
 */

function event(type: RunStreamEvent['type'], payload: Record<string, unknown>, seq = 1): RunStreamEvent {
  return { seq, type, payload, at: 1_000 + seq };
}

function run(state: GenerationState, events: RunStreamEvent[]): GenerationState {
  return events.reduce((accumulator, item) => generationReducer(accumulator, { type: 'event', event: item }), state);
}

describe('generation reducer', () => {
  it('starts idle', () => {
    expect(initialGenerationState.phase).toBe('idle');
    expect(initialGenerationState.pct).toBe(0);
  });

  it('moves to submitting then running', () => {
    const submitted = generationReducer(initialGenerationState, { type: 'submit', instrumentId: 'CN:600519.SH' });
    expect(submitted.phase).toBe('submitting');
    expect(submitted.instrumentId).toBe('CN:600519.SH');

    const queued = generationReducer(submitted, { type: 'queued', runId: 'run-1' });
    expect(queued.phase).toBe('running');
    expect(queued.runId).toBe('run-1');
  });

  it('advances through all six stages monotonically', () => {
    let state = generationReducer(
      generationReducer(initialGenerationState, { type: 'submit', instrumentId: 'x' }),
      { type: 'queued', runId: 'r' },
    );
    const stages: [string, number][] = [
      ['resolve', 5],
      ['snapshot', 15],
      ['prepare', 20],
      ['research', 70],
      ['validate', 85],
      ['persist', 100],
    ];
    const seen: number[] = [];
    stages.forEach(([stage, pct], index) => {
      state = run(state, [event('run.stage', { stage, pct, label: stage }, index)]);
      seen.push(state.pct);
    });
    expect(seen).toEqual([5, 15, 20, 70, 85, 100]);
    expect(state.stage).toBe('persist');
  });

  it('never lets the progress bar go backwards', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [event('run.stage', { stage: 'research', pct: 60 })]);
    state = run(state, [event('run.stage', { stage: 'prepare', pct: 20 })]);
    expect(state.pct).toBe(60);
  });

  it('records tool calls without unbounded growth', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    for (let index = 0; index < 60; index += 1) {
      state = run(state, [
        event('agent.tool', { name: 'read_file', input: '{}', phase: 'result', outputPreview: 'x' }, index),
      ]);
    }
    expect(state.tools.length).toBeLessThanOrEqual(24);
  });

  it('keeps logs bounded and preserves the newest lines', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    for (let index = 0; index < 120; index += 1) {
      state = run(state, [event('run.log', { level: 'info', message: `line ${index}` }, index)]);
    }
    expect(state.logs.length).toBeLessThanOrEqual(50);
    expect(state.logs.at(-1)?.message).toBe('line 119');
  });

  it('accumulates streamed text', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [event('agent.text', { delta: '第一段。' })]);
    state = run(state, [event('agent.text', { delta: '第二段。' }, 2)]);
    expect(state.streamText).toBe('第一段。第二段。');
  });

  it('finishes on run.done with the report id', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [
      event('run.done', {
        reportId: 'rep-1',
        version: 3,
        status: 'ready',
        costUsd: 0.42,
        costKnown: true,
        durationMs: 96_000,
      }),
    ]);
    expect(state.phase).toBe('done');
    expect(state.reportId).toBe('rep-1');
    expect(state.version).toBe(3);
    expect(state.costKnown).toBe(true);
    expect(state.pct).toBe(100);
    expect(state.error).toBeNull();
  });

  it('keeps cost unknown when the CLI did not price the run', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [event('run.done', { reportId: 'rep-1', version: 1, costKnown: false })]);
    expect(state.costKnown).toBe(false);
    expect(state.costUsd).toBeNull();
  });

  it('enters error on run.error', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [event('run.error', { code: 'result_schema_unsatisfied', message: 'structured was null' })]);
    expect(state.phase).toBe('error');
    expect(state.error?.code).toBe('result_schema_unsatisfied');
  });

  it('maps a cancelled run to the cancelled phase, not an error', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [event('run.error', { code: 'cancelled', message: '用户取消' })]);
    expect(state.phase).toBe('cancelled');
  });

  it('records a submit exception', () => {
    const state = generationReducer(initialGenerationState, {
      type: 'exception',
      code: 'run_conflict',
      message: '该标的已有进行中的生成任务',
    });
    expect(state.phase).toBe('error');
    expect(state.error?.code).toBe('run_conflict');
  });

  it('ignores unknown event types for forward compatibility', () => {
    const state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    const after = run(state, [event('agent.queue_pressure' as RunStreamEvent['type'], { value: 1 })]);
    expect(after).toEqual(state);
  });

  it('resets to the initial state', () => {
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'r' });
    state = run(state, [event('run.done', { reportId: 'x', version: 1 })]);
    expect(generationReducer(state, { type: 'reset' })).toEqual(initialGenerationState);
  });
});

describe('SSE transport helpers', () => {
  it('puts the token and the resume cursor in the query string', () => {
    const url = buildEventsUrl('run-1', 'abc.def', 42);
    expect(url).toContain('/api/v1/runs/run-1/events');
    expect(url).toContain('token=abc.def');
    expect(url).toContain('after=42');
  });

  it('omits an empty cursor', () => {
    const url = buildEventsUrl('run-1', 'tok', 0);
    expect(url).not.toContain('after=');
  });

  it('backs off exponentially but stays bounded', () => {
    expect(retryDelay(0)).toBe(400);
    expect(retryDelay(1)).toBe(800);
    expect(retryDelay(2)).toBe(1600);
    expect(retryDelay(10)).toBeLessThanOrEqual(8000);
  });
});
