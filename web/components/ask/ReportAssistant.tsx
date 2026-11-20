'use client';

import dynamic from 'next/dynamic';
import { useEffect, useRef, useState } from 'react';

import { useMe } from '@/lib/auth';
import { useUiStore } from '@/lib/store';
import { notify } from '../ui/Toast';

import { AssistantLauncher } from './AssistantLauncher';
import { AskPanelBoundary, AskPanelLoading } from './AskPanelBoundary';

const AskPanel = dynamic(() => import('./AskPanel').then((m) => m.AskPanel), {
  ssr: false,
  loading: () => <AskPanelLoading open={useUiStore.getState().assistantOpen} onClose={() =>
    useUiStore.getState().setAssistantOpen(false)} />,
});

interface ReportAssistantProps {
  reportId: string;
  reportLabel: string;
  deepAvailable: boolean;
  conversationAvailable: boolean;
  questionMaxChars: number;
  dailyLimit: number;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
  reportRootRef: React.RefObject<HTMLElement | null>;
  onJumpToBlock: (blockId: string, sectionId: string) => void;
  onJumpToSource: (sourceId: string) => void;
}

/** Report-scoped integration keeps chat code lazy and visibility in one store. */
export function ReportAssistant(props: ReportAssistantProps) {
  const open = useUiStore((state) => state.assistantOpen);
  const setOpen = useUiStore((state) => state.setAssistantOpen);
  const [mounted, setMounted] = useState(false);
  const [returnFocus, setReturnFocus] = useState(true);
  const [hasUnread, setHasUnread] = useState(false);
  const [isActive, setIsActive] = useState(false);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const previousReport = useRef(props.reportId);
  const switched = previousReport.current !== props.reportId;
  const me = useMe();
  const identityKey = me.user?.id
    ?? (me.authMode === 'open' ? 'open' : 'guest');
  useEffect(() => {
    if (open) { setMounted(true); setReturnFocus(true); }
  }, [open]);
  useEffect(() => {
    setHasUnread(false); setIsActive(false);
    previousReport.current = props.reportId;
  }, [props.reportId, identityKey]);
  useEffect(() => () => setOpen(false), [setOpen]);
  return <>
    <AssistantLauncher ref={launcherRef} open={open}
      isActive={isActive} hasUnread={hasUnread}
      onClick={(event) => {
        props.triggerRef.current = event.currentTarget;
        setReturnFocus(true);
        setOpen(!open);
      }} />
    {mounted ? <AskPanelBoundary key={`${identityKey}:${props.reportId}`} open={open}
      triggerRef={props.triggerRef}
      onClose={() => setOpen(false)}>
      <AskPanel {...props} identityKey={identityKey}
        key={`${identityKey}:${props.reportId}`} open={open}
        reportLabel={`${switched ? '已切换到报告 · ' : ''}${props.reportLabel}`}
        onOpenChange={setOpen} returnFocus={returnFocus}
        onUnreadChange={setHasUnread} onActivityChange={setIsActive}
        onJumpToBlock={(blockId, sectionId) => {
          setReturnFocus(false);
          setOpen(false);
          props.onJumpToBlock(blockId, sectionId);
          focusReportBlock(props.reportRootRef, blockId);
        }} />
    </AskPanelBoundary> : null}
  </>;
}

function focusReportBlock(
  reportRootRef: React.RefObject<HTMLElement | null>, blockId: string,
) {
  let frames = 0;
  const focusTarget = () => {
    const target = reportRootRef.current?.querySelector<HTMLElement>(
      `[data-block-id="${CSS.escape(blockId)}"]`,
    );
    if (target) {
      target.tabIndex = -1;
      target.scrollIntoView({ block: 'center' });
      target.focus({ preventScroll: true });
    } else if (++frames < 60) requestAnimationFrame(focusTarget);
    else notify.info('段落已不可用');
  };
  requestAnimationFrame(focusTarget);
}
