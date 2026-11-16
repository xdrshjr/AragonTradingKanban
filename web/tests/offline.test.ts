import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { copy } from '@/lib/copy';
import {
  isOffline,
  offlineBadgeText,
  readReportGeneratedAt,
  registerOfflineSupport,
  REPORT_GENERATED_AT_META,
  SERVICE_WORKER_PATH,
  subscribeOnlineStatus,
} from '@/lib/offline';

/**
 * AC-37 / AC-45：离线徽标与在线状态。
 *
 * 这里测的是**前端可测的那一半**：`navigator.onLine` → `isOffline()`、事件订阅、
 * `<meta name="aragon-report-generated-at">` → 「离线副本 · 生成于 …」。
 * 另一半（SW 真的能返回离线 HTML）只能在 Playwright 的 `context.setOffline(true)` 里验，
 * 因为 jsdom 没有 service worker，也没有 HTTP 缓存。
 *
 * 关键约束（v4 V4-P1-9）：徽标的时间**不来自响应头**，只来自页面 meta ——
 * 默认 API 是跨源的，SW 既缓存不到也给不出 `X-From-Cache`。
 */

function setOnline(value: boolean) {
  Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => value });
}

function mountMeta(content: string | null) {
  document.head.querySelectorAll(`meta[name="${REPORT_GENERATED_AT_META}"]`).forEach((node) => node.remove());
  if (content === null) return;
  const meta = document.createElement('meta');
  meta.setAttribute('name', REPORT_GENERATED_AT_META);
  meta.setAttribute('content', content);
  document.head.appendChild(meta);
}

beforeEach(() => {
  setOnline(true);
  mountMeta(null);
});

afterEach(() => {
  vi.restoreAllMocks();
  mountMeta(null);
  setOnline(true);
});

describe('isOffline(): navigator.onLine 是唯一真源', () => {
  it('在线时为 false', () => {
    setOnline(true);
    expect(isOffline()).toBe(false);
  });

  it('断网时为 true', () => {
    setOnline(false);
    expect(isOffline()).toBe(true);
  });
});

describe('subscribeOnlineStatus()', () => {
  it('online / offline 事件各触发一次，参数是布尔而不是事件对象', () => {
    const seen: boolean[] = [];
    const unsubscribe = subscribeOnlineStatus((online) => seen.push(online));

    setOnline(false);
    window.dispatchEvent(new Event('offline'));
    setOnline(true);
    window.dispatchEvent(new Event('online'));

    expect(seen).toEqual([false, true]);
    unsubscribe();
  });

  it('退订之后不再触发（监听器真的摘掉了）', () => {
    const cb = vi.fn();
    const unsubscribe = subscribeOnlineStatus(cb);
    unsubscribe();

    window.dispatchEvent(new Event('offline'));
    window.dispatchEvent(new Event('online'));

    expect(cb).not.toHaveBeenCalled();
  });

  it('订阅不会立即回调：调用方先用 isOffline() 播下初始状态', () => {
    const cb = vi.fn();
    subscribeOnlineStatus(cb)();
    expect(cb).not.toHaveBeenCalled();
  });
});

describe('readReportGeneratedAt()', () => {
  it('读到 meta 的 content', () => {
    mountMeta('2026-02-10T01:00:00Z');
    expect(readReportGeneratedAt()).toBe('2026-02-10T01:00:00Z');
  });

  it('没有 meta 时返回 null（不编时间）', () => {
    expect(readReportGeneratedAt()).toBeNull();
  });

  it('content 为空白时返回 null', () => {
    mountMeta('   ');
    expect(readReportGeneratedAt()).toBeNull();
  });
});

describe('offlineBadgeText(): 「离线副本 · 生成于 …」', () => {
  it('离线 + 有 meta → 带生成时间', () => {
    setOnline(false);
    mountMeta('2026-02-10T01:00:00Z');

    const badge = offlineBadgeText();
    expect(badge).not.toBeNull();
    // 前缀来自 copy.offline.badgeWithTime，`{time}` 必须被替换掉
    expect(badge).toMatch(/^离线副本 · 生成于 /);
    expect(badge).not.toContain('{time}');
    expect(badge).not.toBe(copy.offline.badge);
    // 时区因机器而异，只断言"是个可读时间"（含年份与冒号分隔的时分）
    expect(badge).toMatch(/\d{4}/);
    expect(badge).toMatch(/\d{2}:\d{2}/);
  });

  it('在线 → null（页面上不该出现离线标识）', () => {
    setOnline(true);
    mountMeta('2026-02-10T01:00:00Z');
    expect(offlineBadgeText()).toBeNull();
  });

  it('离线但没有 meta（报告还没渲染出生成时间）→ null，不冒充离线副本', () => {
    setOnline(false);
    expect(offlineBadgeText()).toBeNull();
  });

  it('离线 + meta 内容是坏字符串 → 回退成原始字符串，而不是破折号或空串', () => {
    setOnline(false);
    mountMeta('不是时间');
    const badge = offlineBadgeText();
    expect(badge).toBe('离线副本 · 生成于 不是时间');
  });
});

describe('registerOfflineSupport(): 只在生产注册', () => {
  it('测试环境（NODE_ENV=test）下完全不碰 navigator.serviceWorker', () => {
    const register = vi.fn().mockResolvedValue({} as ServiceWorkerRegistration);
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      get: () => ({ register }),
    });
    expect(process.env.NODE_ENV).not.toBe('production');

    expect(() => registerOfflineSupport()).not.toThrow();
    expect(register).not.toHaveBeenCalled();

    // 清理：别把假的 SW 留在 navigator 上影响别的测试
    Reflect.deleteProperty(navigator as unknown as Record<string, unknown>, 'serviceWorker');
  });

  it('路径常量指向 /sw.js（public 目录直出）', () => {
    expect(SERVICE_WORKER_PATH).toBe('/sw.js');
  });
});
