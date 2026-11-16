#!/usr/bin/env node
/**
 * `npm run seed` — create the three demo instruments through the public API so
 * the left rail is not empty on a fresh database. Idempotent: resolving an
 * existing symbol returns the existing instrument.
 */
const API = process.env.NEXT_PUBLIC_API_BASE || `http://127.0.0.1:${process.env.PY_API_PORT || '8000'}`;
const TOKEN = process.env.API_TOKEN || '';

const QUERIES = ['贵州茅台', '宁德时代', 'AAPL'];

async function post(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(TOKEN ? { authorization: `Bearer ${TOKEN}` } : {}),
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${path} → ${res.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

async function main() {
  process.stdout.write(`[seed] API ${API}\n`);
  for (const query of QUERIES) {
    try {
      const resolved = await post('/api/v1/symbols/resolve', { query });
      const inst = resolved.instrument;
      await post('/api/v1/watchlist', { instrumentId: inst.id });
      process.stdout.write(`[seed] ${inst.id}  ${inst.name}${resolved.created ? ' (created)' : ''}\n`);
    } catch (err) {
      process.stderr.write(`[seed] ${query} failed: ${err.message}\n`);
      process.exitCode = 1;
    }
  }
}

main();
