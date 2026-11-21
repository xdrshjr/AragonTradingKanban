'use client';

import { useEffect } from 'react';

import { copyWorkflow } from './copy.workflow';
import { hasPendingWrites, resumeSync, useWorkflowStore } from './store';

/**
 * 把保存队列接到浏览器事件上（编辑器挂载期间有效）：
 *
 * * `beforeunload`：**仅当队列非空**才提示"有未保存修改"；
 * * `online`：网络恢复后让离线期间缓存的批次按序重放；
 * * 卸载时 `flush()`：尽量把最后一批写完再离开。
 */
export function useWorkflowSync(): void {
  const notify = useWorkflowStore((state) => state.notify);

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent): string | undefined => {
      if (!hasPendingWrites()) return undefined;
      event.preventDefault();
      event.returnValue = copyWorkflow.unsaved;
      return copyWorkflow.unsaved;
    };
    const onOnline = (): void => resumeSync();
    const onOffline = (): void => notify('info', copyWorkflow.toasts.offlineQueued);

    window.addEventListener('beforeunload', onBeforeUnload);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      void useWorkflowStore.getState().flush();
    };
  }, [notify]);
}
