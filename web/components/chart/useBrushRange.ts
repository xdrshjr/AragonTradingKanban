'use client';

import { useEffect, useRef, useState } from 'react';

import { ApiError, api } from '@/lib/api';
import { copy } from '@/lib/copy';
import type { BlockRangeResponse } from '@/lib/types';

/**
 * K 线框选 → 报告反查（spec §19.3.3 / §19.5.2 / AC-54 / AC-64）。
 *
 * 为什么是「防抖 + 代号」而不是 `AbortController`：`api.reportBlocks` 走的是全项目统一的
 * `request()`（超时由 `AbortSignal.timeout` 兜底，签名里没有 `signal`），所以撤回只能靠
 * 两件已经足够的事：
 *  1. **防抖窗口内不发请求**——手指还在拖的时候网络是闲的；250ms 也正好是"框选结束"到
 *     "用户想看答案"之间的自然停顿（spec §19.3.3 写死 250ms）；
 *  2. **代号（generation）作废在途结果**——`from/to` 每变一次就 +1，迟到的响应回来发现
 *     代号不对就丢弃，不会把上一段区间的段落贴到新的区间上（`useAskStream` 同一个写法）。
 *
 * 不轮询：报告是静态文档，区间不变就没有新答案；轮询只会让"反查"变成一份会自己变化的清单。
 */

export type BrushRangeState = 'idle' | 'loading' | 'ready' | 'error' | 'disabled';

export interface UseBrushRangeOptions {
  reportId: string;
  /** 闭区间左端，`YYYY-MM-DD`（与 `?range=A..B` 及 `/reports/{id}/blocks` 同形）。 */
  from: string;
  to: string;
  /** `false` 时一个请求都不发（`capabilities.features.rangeQuery=false` 或区间为空）。 */
  enabled?: boolean;
  debounceMs?: number;
}

export interface UseBrushRangeResult {
  state: BrushRangeState;
  data: BlockRangeResponse | null;
  error: string | null;
}

/** spec §19.3.3 的防抖窗口；只在测试里调小，不给产品留可调项。 */
const DEFAULT_DEBOUNCE_MS = 250;

/**
 * 408 是 `api.ts` 对超时的统一映射。区间反查超时最可能的原因是这段行情在报告里
 * 没被提及（后端要遍历全部块的日期区间），所以文案用 `timeout`（"未找到"而不是"失败"）。
 */
function messageFor(error: unknown): string {
  if (error instanceof ApiError && error.status === 408) return copy.range.timeout;
  return copy.range.failed;
}

export function useBrushRange({
  reportId,
  from,
  to,
  enabled = true,
  debounceMs = DEFAULT_DEBOUNCE_MS,
}: UseBrushRangeOptions): UseBrushRangeResult {
  const [result, setResult] = useState<UseBrushRangeResult>(() => ({
    state: enabled ? 'idle' : 'disabled',
    data: null,
    error: null,
  }));
  /** 每次区间变化 +1：`await` 之后回来的请求若发现代号变了，必须放弃结果。 */
  const generationRef = useRef(0);

  useEffect(() => {
    const generation = (generationRef.current += 1);
    // 缺任一端都不是一次有效的区间查询：宁可停在 idle，也不要发一个必被 422 的请求。
    const usable = enabled && reportId.length > 0 && from.length > 0 && to.length > 0;

    if (!usable) {
      setResult(
        enabled
          ? { state: 'idle', data: null, error: null }
          : { state: 'disabled', data: null, error: null },
      );
      return;
    }

    setResult({ state: 'loading', data: null, error: null });

    const timer = setTimeout(() => {
      void (async () => {
        try {
          const data = await api.reportBlocks(reportId, from, to);
          if (generationRef.current !== generation) return;
          setResult({ state: 'ready', data, error: null });
        } catch (error: unknown) {
          if (generationRef.current !== generation) return;
          setResult({ state: 'error', data: null, error: messageFor(error) });
        }
      })();
    }, Math.max(0, debounceMs));

    // 依赖变化或卸载：清掉还没到点的定时器（防抖窗口内的请求根本不该发生）。
    return () => clearTimeout(timer);
  }, [reportId, from, to, enabled, debounceMs]);

  // 卸载后不许再落状态：在途结果回来时代号已变，直接丢弃（React 不需要警告也知道该扔掉）。
  useEffect(
    () => () => {
      generationRef.current += 1;
    },
    [],
  );

  return result;
}
