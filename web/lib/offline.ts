/**
 * 离线只读（AC-37 / AC-45 / §18.3.7 v4 修订 V4-P1-9）。
 *
 * 缓存的对象是**同源导航文档**（`/[symbol]` 的 HTML —— 里面本来就含整份报告），
 * 由 `web/public/sw.js` 负责；本模块只做四件事：
 *
 * 1. `registerOfflineSupport()` —— **仅生产**注册 SW（开发期不注册，免得干扰 HMR）；
 * 2. `isOffline()` —— 单一在线/离线真源（`navigator.onLine`）；
 * 3. `subscribeOnlineStatus()` —— 事件订阅，返回退订函数；
 * 4. `readReportGeneratedAt()` / `offlineBadgeText()` —— 从页面
 *    `<meta name="aragon-report-generated-at">` 取生成时间，拼出
 *    「离线副本 · 生成于 …」徽标文案。
 *
 * 为什么不看响应头：默认 `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000` 是**跨源**的，
 * 首屏报告还是 RSC 服务端直出 —— 浏览器侧 SW 既看不到那次 fetch，也无法给跨源
 * 响应贴 `X-From-Cache`。所以"过期必须自证过期"的取证位置改成了页面里的 meta 标签。
 *
 * 集成方需要做的两件事（不在本文件里）：
 * - 在根布局/工作区挂一次 `registerOfflineSupport()`；
 * - 在 `/[symbol]` 的渲染里输出 `<meta name="aragon-report-generated-at" content={report.generatedAt}>`。
 */

import { copy, fill } from './copy';
import { dateTime } from './format';

/** meta 标签名 —— SW/文档/测试共用同一个字面量，别再手写第二处。 */
export const REPORT_GENERATED_AT_META = 'aragon-report-generated-at';

/** service worker 脚本路径（`web/public/sw.js`）。 */
export const SERVICE_WORKER_PATH = '/sw.js';

function hasServiceWorker(): boolean {
  return typeof navigator !== 'undefined' && Boolean(navigator.serviceWorker);
}

/**
 * 注册离线支持。**非生产环境是彻底的 no-op**（返回前不碰 `navigator`），
 * 没有 SW 能力的浏览器同理。注册失败只影响离线能力，绝不影响在线使用，
 * 因此吞掉异常而不是往控制台喷红线。
 */
export function registerOfflineSupport(): void {
  if (process.env.NODE_ENV !== 'production') return;
  if (typeof window === 'undefined' || !hasServiceWorker()) return;

  try {
    void navigator.serviceWorker.register(SERVICE_WORKER_PATH).catch(() => {
      /* 注册失败（隐私模式 / 非安全上下文）：在线功能不受影响 */
    });
  } catch {
    /* 某些浏览器在非 https 下直接 throw，同上了 */
  }
}

/** 当前是否离线。SSR / 无 `navigator` 时一律当作在线（不谎报离线）。 */
export function isOffline(): boolean {
  if (typeof navigator === 'undefined') return false;
  return navigator.onLine === false;
}

/**
 * 订阅在线/离线切换。返回退订函数。
 *
 * **不会立即回调一次** —— 调用方先读 `isOffline()` 初始化状态，再用本函数跟随后续变化
 * （这样 React effect 里不会出现"订阅即 setState"的多余渲染）。
 */
export function subscribeOnlineStatus(cb: (online: boolean) => void): () => void {
  if (typeof window === 'undefined') return () => {};

  const onOnline = () => cb(true);
  const onOffline = () => cb(false);

  window.addEventListener('online', onOnline);
  window.addEventListener('offline', onOffline);

  return () => {
    window.removeEventListener('online', onOnline);
    window.removeEventListener('offline', onOffline);
  };
}

/**
 * 读页面里的 `<meta name="aragon-report-generated-at" content="…">`。
 * 没有标签、内容为空、SSR 阶段 → `null`（"没有证据"就必须返回 null，不能编一个时间）。
 */
export function readReportGeneratedAt(): string | null {
  if (typeof document === 'undefined') return null;
  const meta = document.querySelector<HTMLMetaElement>(`meta[name="${REPORT_GENERATED_AT_META}"]`);
  const content = meta?.getAttribute('content')?.trim();
  return content ? content : null;
}

/**
 * 「离线副本 · 生成于 …」；在线、或没有 meta 时间证据时返回 `null`。
 *
 * 返回 `null` 而不是空串，是为了让调用方写成 `{badge ? <span>{badge}</span> : null}`
 * —— 在线时页面上**不存在**离线标识，而不是存在一个空节点。
 */
export function offlineBadgeText(): string | null {
  if (!isOffline()) return null;
  const generatedAt = readReportGeneratedAt();
  if (!generatedAt) return null;

  const readable = dateTime(generatedAt);
  // dateTime 解析失败会返回 '—'；此时宁可显示原始 ISO 字符串，也不显示破折号。
  return fill(copy.offline.badgeWithTime, { time: readable === '—' ? generatedAt : readable });
}
