'use client';

/**
 * ⚠️ 本文件现在**不写任何东西**：A5 的两件事各有唯一归属——
 *
 * - service worker 的注册在 `Providers`（根布局，每一页都注册，非生产 no-op）；
 * - `<meta name="aragon-report-generated-at">`（"离线副本"徽标的唯一时间证据，
 *   ★V4-P1-9）在 `WorkspaceClient`，因为切换版本是纯客户端行为：写在 RSC 里会
 *   一直报着上一版的时间。
 *
 * 保留组件是为了让 `/[symbol]` 的页面结构不变；若下游要加 A5 的第三件事，
 * 先想清楚它属于"每页一次"还是"打开版本变化时"，再决定放哪。
 */
export function ReportMeta() {
  return null;
}
