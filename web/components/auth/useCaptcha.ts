'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import type { CaptchaChallenge } from '@/lib/types';

export interface UseCaptchaResult {
  challenge: CaptchaChallenge | null;
  /** 图片**签发**的时刻（ms）——注册请求的 `issuedAt`，服务端据此判"过快"。 */
  issuedAt: number | null;
  loading: boolean;
  /** 取图失败的错误码（`captcha_unavailable` / `network_error` …）。 */
  failure: string | null;
  refresh: () => Promise<void>;
  /** 服务端的失败响应已经附带了新图（`detail.challenge`）：直接采用，省一次请求。 */
  adopt: (challenge: CaptchaChallenge) => void;
  clear: () => void;
}

/**
 * 图形验证码的取图与换图。`enabled` 为假时不请求（例如注册页填了邀请码、登录页还没被要求验证码）。
 *
 * 过期的响应不会覆盖较新的一张（连点"换一张"时用递增序号丢弃迟到者）。
 */
export function useCaptcha(purpose: 'register' | 'login' | 'recover', enabled: boolean): UseCaptchaResult {
  const [challenge, setChallenge] = useState<CaptchaChallenge | null>(null);
  const [issuedAt, setIssuedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const ticket = useRef(0);

  const adopt = useCallback((next: CaptchaChallenge) => {
    ticket.current += 1;
    setChallenge(next);
    setIssuedAt(Date.now());
    setFailure(null);
    setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    const mine = ++ticket.current;
    setLoading(true);
    try {
      const next = await api.captcha(purpose);
      if (mine !== ticket.current) return;
      setChallenge(next);
      setIssuedAt(Date.now());
      setFailure(null);
    } catch (error) {
      if (mine !== ticket.current) return;
      setChallenge(null);
      setIssuedAt(null);
      setFailure(error instanceof ApiError ? error.code : 'network_error');
    } finally {
      if (mine === ticket.current) setLoading(false);
    }
  }, [purpose]);

  const clear = useCallback(() => {
    ticket.current += 1;
    setChallenge(null);
    setIssuedAt(null);
    setFailure(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (enabled && !challenge && !failure && !loading) void refresh();
    // 只在 `enabled` 翻转时触发；`challenge/failure/loading` 的变化不应再次取图。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return { challenge, issuedAt, loading, failure, refresh, adopt, clear };
}
