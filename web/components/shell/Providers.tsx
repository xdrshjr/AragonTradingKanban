'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';

import { ToastProvider } from '@/components/ui/Toast';
import { TooltipProvider } from '@/components/ui/Tooltip';

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

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={200} skipDelayDuration={400} disableHoverableContent>
        {children}
        <ToastProvider />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
