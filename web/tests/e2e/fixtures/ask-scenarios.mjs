import { readFileSync } from 'node:fs';

const permissions = JSON.parse(readFileSync(new URL(
  '../../../../contracts/permissions.json', import.meta.url,
), 'utf8'));
const expand = (role) => permissions.roles[role].flatMap((item) =>
  item.startsWith('@') ? expand(item.slice(1)) : [item]);
const scenarios = new Map();

function scenarioOf(req) {
  const cookie = /(?:^|;\s*)aragon_sid=(ask-e2e-[\w-]+)/
    .exec(req.headers.cookie ?? '')?.[1];
  if (!cookie) {
    // EventSource uses the ask-bound signature without cross-origin cookies.
    const token = new URL(req.url, 'http://127.0.0.1').searchParams.get('token');
    return [...scenarios.values()].find((scenario) =>
      scenario.asks.some((ask) => token === tokenFor(scenario, ask.id))) ?? null;
  }
  if (!scenarios.has(cookie)) scenarios.set(cookie, {
    key: cookie, asks: [], posts: [],
  });
  return scenarios.get(cookie);
}

const tokenFor = (scenario, askId) => `token-${scenario.key}-${askId}`;

function me() {
  return {
    authMode: 'enforced', authenticated: true,
    user: {
      id: 'assistant-user', username: 'researcher', role: 'user',
      displayName: '研究员', status: 'active',
      createdAt: '2026-11-20T00:00:00Z',
      mustChangePassword: false, passwordIsDefault: false,
    },
    permissions: expand('user'),
    quota: {
      report: quotaBucket(), deepAsk: quotaBucket(), tz: 'Asia/Shanghai',
    },
  };
}

function quotaBucket() {
  return { remaining: 20, limit: 20, usedToday: 0, bonus: 0,
    resetAt: '2026-11-21T00:00:00+08:00', pendingRequest: null };
}

function createAsk(ctx, scenario, helpers) {
  const id = `ask-${scenario.asks.length + 1}`;
  const reportId = decodeURIComponent(ctx.path.split('/')[4]);
  const question = String(ctx.body.question).normalize('NFKC').trim();
  const mode = ctx.body.mode ?? 'cite';
  const contextAskIds = ctx.body.contextAskIds ?? [];
  scenario.posts.push({ question, mode, contextAskIds });
  const cite = helpers.citeAnswer(reportId, question);
  const ask = {
    ...cite, id, contextAskIds,
    createdAt: new Date(Date.UTC(2026, 10, 20, 0, 0,
      scenario.asks.length + 1)).toISOString(),
    mode, status: mode === 'deep' ? 'queued' : 'done',
    answer: mode === 'deep' ? null : cite.answer,
  };
  scenario.asks.push(ask);
  helpers.send(ctx.res, mode === 'deep' ? 202 : 200,
    mode === 'deep' ? {
      askId: id, reportId, status: 'queued', mode, contextAskIds,
    } : ask);
}

function streamAsk(ctx, scenario, helpers) {
  const ask = scenario.asks.find((item) => item.id === ctx.askId);
  if (!ask) return helpers.sendError(ctx.res, 404, 'not_found', '问题不存在');
  if (ctx.query.get('token') !== tokenFor(scenario, ask.id)) {
    return helpers.sendError(ctx.res, 401, 'unauthorized', '签名不匹配');
  }
  const answerText = ask.contextAskIds.length
    ? '刚才第二点的估值假设依赖报告中的盈利与增长证据 [S3]。'
    : '报告的核心结论有两点：第一，盈利稳定；第二，估值依赖增长假设 [S3]。';
  ask.status = 'running';
  ctx.res.writeHead(200, {
    'access-control-allow-origin': 'http://127.0.0.1:3010',
    'access-control-allow-credentials': 'true',
    'content-type': 'text/event-stream', 'cache-control': 'no-cache',
  });
  const frames = [
    ['ask.stage', { stage: 'research', pct: 30, label: '正在核对报告证据…' }],
    ['ask.delta', { text: answerText }],
    ['ask.done', { askId: ask.id, grounded: true }],
  ];
  const after = Number(ctx.query.get('after') ?? 0);
  let index = 0;
  const timer = setInterval(() => {
    const seq = index + 1;
    const [event, payload] = frames[index++];
    if (seq > after) ctx.res.write(`id: ${seq}\nevent: ${event}\ndata: ${
      JSON.stringify({ seq, ...payload })}\n\n`);
    if (index === frames.length) {
      ask.status = 'done';
      ask.answer = {
        answerText, hint: null, hits: [],
        citations: [{ sourceId: 'S3', title: '季度报告', publisher: '上交所' }],
      };
      clearInterval(timer);
      ctx.res.end();
    }
  }, 50);
  ctx.req.on('close', () => clearInterval(timer));
}

/** Cookie-isolated bounded assistant routes; ordinary fixtures keep cite mode. */
export function handleAskScenario(ctx, helpers) {
  const scenario = scenarioOf(ctx.req);
  if (!scenario) {
    if (ctx.path !== '/api/v1/auth/me') return false;
    helpers.send(ctx.res, 200, {
      authMode: 'open', authenticated: true, user: null,
      permissions: [...new Set(expand('root'))], quota: null,
    });
    return true;
  }
  const { path, req, res } = ctx;
  if (path === '/api/v1/auth/me') helpers.send(res, 200, me());
  else if (path === '/api/v1/reports' && ctx.query.get('scope') === 'latest') {
    const items = Array.from({ length: 6 }, (_, index) => {
      const item = helpers.libraryItem(1);
      return { ...item, report: { ...item.report,
        id: `landing-report-${index}`, headline: `研究报告 ${index + 1} · ${
          item.report.headline.repeat(3)}` },
      };
    });
    helpers.send(res, 200, { items, total: 6, counts: {}, active: [],
      nextCursor: null });
  }
  else if (path === '/api/v1/capabilities') helpers.send(res, 200, {
    ...helpers.capabilities,
    features: { ...helpers.capabilities.features,
      askDeep: true, askConversation: true },
    limits: { ...helpers.capabilities.limits, askMaxQuestionChars: 500 },
  });
  else if (path === '/api/v1/test/assistant' && req.method === 'DELETE') {
    scenario.asks = []; scenario.posts = [];
    helpers.send(res, 200, { ok: true });
  } else if (path === '/api/v1/test/assistant') {
    helpers.send(res, 200, { posts: scenario.posts, asks: scenario.asks });
  } else if (/\/reports\/[^/]+\/asks$/.test(path)) {
    const reportId = decodeURIComponent(path.split('/')[4]);
    const items = scenario.asks.filter((ask) => ask.reportId === reportId)
      .slice(-20).reverse();
    helpers.send(res, 200, { items, total: items.length });
  } else if (/\/reports\/[^/]+\/ask$/.test(path) && req.method === 'POST') {
    createAsk(ctx, scenario, helpers);
  } else if (/\/asks\/[^/]+/.test(path)) {
    handleAsk(ctx, scenario, helpers);
  } else return false;
  return true;
}

function handleAsk(ctx, scenario, helpers) {
  const id = decodeURIComponent(ctx.path.split('/')[4]);
  const ask = scenario.asks.find((item) => item.id === id);
  if (!ask) return helpers.sendError(ctx.res, 404, 'not_found', '问题不存在');
  if (ctx.path.endsWith('/events-token')) {
    helpers.send(ctx.res, 200, { token: tokenFor(scenario, id), expiresIn: 60 });
  } else if (ctx.path.endsWith('/events')) {
    streamAsk({ ...ctx, askId: id }, scenario, helpers);
  } else if (ctx.path.endsWith('/cancel')) {
    ask.status = 'cancelled'; ask.errorCode = 'cancelled';
    helpers.send(ctx.res, 200, { status: 'cancelling', runId: id });
  } else helpers.send(ctx.res, 200, ask);
}
