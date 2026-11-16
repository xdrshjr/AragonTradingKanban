'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { ToastProvider } from '@/components/ui/Toast';
import { TooltipProvider } from '@/components/ui/Tooltip';
import { registerOfflineSupport } from '@/lib/offline';

/**
 * Client providers. The QueryClient is created once per browser session so
 * server rendering never shares a cache between requests (§10.2).
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  // A5 / §18.4.5：service worker 在这里注册，也就是**每一页**——落在首页的读者
  // 同样要把壳缓存下来，而不是必须先打开一份报告。非生产环境是 no-op。
  useEffect(() => {
    registerOfflineSupport();
  }, []);

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={200} skipDelayDuration={400} disableHoverableContent>
        {children}
        <ToastProvider />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
