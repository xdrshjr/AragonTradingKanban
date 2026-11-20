'use client';

import { QueryClient, QueryClientContext, useQuery } from '@tanstack/react-query';
import { useCallback, useContext, useEffect, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { useMe, useMeActions } from '@/lib/auth';
import { copy } from '@/lib/copy';
import type { AskMode, AskOut } from '@/lib/types';
import {
  isAskActive, useAskStream, type AskPhase, type AskSubmitInput,
} from './useAskStream';

export interface AskTurnView {
  localId: string;
  askId: string | null;
  question: string;
  mode: AskMode;
  contextAskIds: string[];
  phase: AskPhase;
  result: AskOut | null;
  preview: string;
  error: { code: string; message: string } | null;
}

export interface AskConversationOptions {
  reportId: string;
  identityKey: string;
  open: boolean;
  deepAvailable: boolean;
  conversationAvailable: boolean;
  dailyLimit: number;
  questionMaxChars: number;
}

/** Mirrors normalize_question; counts Unicode code points after NFKC. */
export function normalizeAskQuestion(raw: string): string {
  return raw.normalize('NFKC').replace(/[\r\n\t]/g, ' ').trim();
}

function phaseForAsk(ask: AskOut): AskPhase {
  if (ask.status === 'done') return 'done';
  if (ask.status === 'failed') return 'error';
  if (ask.status === 'cancelled') return 'cancelled';
  return ask.status === 'cancelling' ? 'cancelling' : 'streaming';
}

function canReference(ask: AskOut): boolean {
  if (ask.status !== 'done' || !ask.grounded) return false;
  return ask.mode === 'deep'
    ? Boolean(ask.answer.answerText?.trim())
    : ask.answer.hits.some((hit) => hit.excerpt.trim().length > 0);
}

function turnForAsk(ask: AskOut): AskTurnView {
  return {
    localId: ask.id, askId: ask.id, question: ask.question,
    mode: ask.mode, contextAskIds: ask.contextAskIds ?? [],
    phase: phaseForAsk(ask), result: ask, preview: '',
    error: ask.status === 'failed' || ask.status === 'cancelled'
      ? { code: ask.errorCode ?? ask.status,
        message: ask.errorMessage ?? copy.ask.failed }
      : null,
  };
}

function mergeHistory(previous: AskTurnView[], incoming: AskOut[]) {
  const merged = [...previous];
  for (const ask of incoming) {
    const index = merged.findIndex((turn) => turn.askId === ask.id);
    if (index < 0) {
      merged.push(turnForAsk(ask));
      continue;
    }
    const current = merged[index]!;
    // A stale history snapshot cannot regress a verified terminal result.
    if (current.result && !isAskActive(current.phase)) continue;
    if (isAskActive(phaseForAsk(ask))) continue;
    merged[index] = { ...turnForAsk(ask), localId: current.localId };
  }
  return merged;
}

function trimTurns(turns: AskTurnView[]): AskTurnView[] {
  const trimmed = [...turns];
  while (trimmed.length > 100) {
    const index = trimmed.findIndex((turn) => !isAskActive(turn.phase));
    if (index < 0) break;
    trimmed.splice(index, 1);
  }
  return trimmed;
}

/** Keeps report/identity history in memory and owns the current topic boundary. */
export function useAskConversation(options: AskConversationOptions) {
  const { reportId, identityKey, open, conversationAvailable } = options;
  const questionMaxChars = Number.isInteger(options.questionMaxChars)
    && options.questionMaxChars > 0
    ? Math.min(4000, options.questionMaxChars) : 500;
  const scope = `${identityKey}:${reportId}`;
  const outerClient = useContext(QueryClientContext);
  const [localClient] = useState(() => new QueryClient());
  const client = outerClient ?? localClient;
  const [turns, setTurns] = useState<AskTurnView[]>([]);
  const [draft, setDraft] = useState('');
  const [mode, setSelectedMode] = useState<AskMode>('cite');
  const [hasOpened, setHasOpened] = useState(open);
  const [hasUnread, setHasUnread] = useState(false);
  const [online, setOnline] = useState(() => navigator.onLine);
  const [skippedHistory, setSkippedHistory] = useState(false);
  const [contextCandidateIds, setCandidates] = useState<string[]>([]);
  const [hasStartedNewTopic, setHasStartedNewTopic] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const activeLocal = useRef<string | null>(null);
  const historyInitialized = useRef(false);
  const currentScope = useRef(scope);
  const epoch = useRef(0);
  const startedAt = useRef(0);
  const submissionSnapshot = useRef<AskSubmitInput | null>(null);
  const knownIdsBeforeSubmit = useRef<Set<string>>(new Set());
  const submitting = useRef(false);
  const latestTurns = useRef(turns);
  latestTurns.current = turns;
  const openRef = useRef(open);
  openRef.current = open;
  if (currentScope.current !== scope) {
    currentScope.current = scope;
    epoch.current += 1;
    historyInitialized.current = false;
    activeLocal.current = null;
  }
  const { refreshMe } = useMeActions();
  const me = useMe();
  const deepBucket = me.quota?.deepAsk;
  const stream = useAskStream({
    ...options,
    onAuthFailure: () => { void refreshMe(); },
    onDone: () => {
      void refreshMe();
      if (!openRef.current) setHasUnread(true);
    },
  });
  const streamRef = useRef(stream);
  streamRef.current = stream;
  const historyKey = ['asks', 'mine', identityKey, reportId];
  const history = useQuery({
    queryKey: historyKey,
    queryFn: async ({ signal }) => ({
      scope, response: await api.reportAsks(reportId, 20, {
        scope: 'mine', signal,
      }),
    }),
    enabled: hasOpened && conversationAvailable,
    staleTime: 0, gcTime: 0, retry: false,
    refetchOnWindowFocus: false,
  }, client);

  useEffect(() => {
    setTurns([]);
    setDraft('');
    setSelectedMode('cite');
    setCandidates([]);
    setHasStartedNewTopic(false);
    setHasUnread(false);
    setSkippedHistory(false);
    setReadError(null);
    return () => {
      epoch.current += 1;
      void client.cancelQueries({ queryKey: historyKey });
      client.removeQueries({ queryKey: historyKey });
    };
    // The key changes only at the identity/report boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, identityKey, reportId]);
  useEffect(() => {
    if (open) { setHasOpened(true); setHasUnread(false); }
  }, [open]);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, []);

  useEffect(() => {
    if (!history.data || history.data.scope !== currentScope.current) return;
    const items = [...history.data.response.items].sort((a, b) =>
      a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
    const currentStream = streamRef.current;
    const visibleHistory = currentStream.isActive
      ? items.filter((ask) => ask.id !== currentStream.state.askId) : items;
    setTurns((previous) => trimTurns(mergeHistory(previous, visibleHistory)));
    if (historyInitialized.current) return;
    historyInitialized.current = true;
    setCandidates(items.filter(canReference).map((ask) => ask.id).slice(-6));
    const active = [...items].reverse().find((ask) =>
      isAskActive(phaseForAsk(ask)));
    if (!active || activeLocal.current) return;
    activeLocal.current = active.id;
    void streamRef.current.resume(active.id);
  }, [history.data]);

  useEffect(() => {
    const state = stream.state;
    const localId = activeLocal.current;
    if (!localId || state.phase === 'idle') return;
    setTurns((previous) => trimTurns(previous.filter((turn) =>
      turn.localId === localId || !state.askId
        || turn.askId !== state.askId).map((turn) => {
      if (turn.localId !== localId) return turn;
      return {
        ...turn, askId: state.askId ?? turn.askId,
        question: state.question || turn.question, mode: state.mode,
        contextAskIds: state.contextAskIds,
        phase: state.phase, result: state.result,
        preview: state.streamText, error: state.error,
      };
    })));
    if (!isAskActive(state.phase) && !state.quota) {
      if (state.result && conversationAvailable && canReference(state.result)) {
        const id = state.result.id;
        setCandidates((ids) => [...ids.filter((item) => item !== id), id]
          .slice(-6));
      }
      activeLocal.current = null;
    }
  }, [stream.state, conversationAvailable]);

  const historyStatus: 'idle' | 'loading' | 'ready' | 'error' =
    !conversationAvailable || skippedHistory
    ? 'ready' : history.isError ? 'error'
      : !hasOpened ? 'idle' : history.data ? 'ready' : 'loading';
  const normalized = normalizeAskQuestion(draft);
  const isValid = normalized.length > 0 && [...draft].length <= 4000
    && [...normalized].length <= questionMaxChars
    && !/\p{C}/u.test(normalized);
  const isActive = stream.isActive;
  const hasInvalidContext = stream.state.error?.code === 'invalid_ask_context';
  const canSubmit = online && isValid && !isActive
    && !hasInvalidContext && !stream.state.quota && historyStatus === 'ready';

  const submit = useCallback(async (input: AskSubmitInput = {}) => {
    if (submitting.current || streamRef.current.isActive
      || streamRef.current.state.quota
      || streamRef.current.state.error?.code === 'invalid_ask_context'
      || !online || historyStatus !== 'ready') {
      return;
    }
    const question = normalizeAskQuestion(input.question ?? draft);
    if (!question || [...question].length > questionMaxChars) return;
    if ([...(input.question ?? draft)].length > 4000) return;
    if (/\p{C}/u.test(question)) return;
    const selected = options.deepAvailable ? input.mode ?? mode : 'cite';
    const contextAskIds = selected === 'deep' && conversationAvailable
      ? [...(input.contextAskIds ?? contextCandidateIds)].slice(-6) : [];
    const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const snapshot = { question, mode: selected, contextAskIds };
    submitting.current = true;
    activeLocal.current = localId;
    submissionSnapshot.current = snapshot;
    knownIdsBeforeSubmit.current = new Set(latestTurns.current.flatMap(
      (turn) => turn.askId ? [turn.askId] : [],
    ));
    startedAt.current = Date.now();
    setTurns((previous) => trimTurns([...previous, {
      localId, askId: null, ...snapshot, phase: 'submitting',
      result: null, preview: '', error: null,
    }]));
    if (input.question === undefined) setDraft('');
    try {
      await streamRef.current.submit(snapshot);
    } finally {
      submitting.current = false;
    }
  }, [conversationAvailable, contextCandidateIds, draft, historyStatus,
    mode, online, options.deepAvailable, questionMaxChars]);

  const reloadHistory = useCallback(async () => {
    if (!conversationAvailable) return;
    const generation = epoch.current;
    const loaded = await history.refetch();
    if (generation !== epoch.current || !loaded.data) return;
    const state = streamRef.current.state;
    if (state.phase !== 'submission-unknown') return;
    const snapshot = submissionSnapshot.current;
    if (!snapshot) return;
    const matches = loaded.data.response.items.filter((ask) => {
      const at = Date.parse(ask.createdAt);
      return !knownIdsBeforeSubmit.current.has(ask.id)
        && normalizeAskQuestion(ask.question) === snapshot.question
        && ask.mode === snapshot.mode && at >= startedAt.current - 2000
        && at <= Date.now() + 10_000;
    });
    if (matches.length !== 1) return;
    const matched = matches[0]!;
    setTurns((previous) => previous.filter((turn) =>
      turn.askId !== matched.id || turn.localId === activeLocal.current));
    await streamRef.current.resume(matched.id);
  }, [conversationAvailable, history]);

  const newTopic = useCallback(() => {
    if (streamRef.current.isActive) return;
    setCandidates([]);
    setHasStartedNewTopic(true);
    setDraft('');
    streamRef.current.reset();
  }, []);
  const startWithoutHistory = useCallback(() => {
    if (streamRef.current.isActive) return;
    historyInitialized.current = true;
    setSkippedHistory(true);
    setCandidates([]);
    setHasStartedNewTopic(true);
  }, []);
  const retry = useCallback(async (turn: AskTurnView) => {
    await submit({ question: turn.question, mode: turn.mode,
      contextAskIds: [...turn.contextAskIds] });
  }, [submit]);
  const declineFallback = useCallback(() => {
    const question = streamRef.current.state.question;
    streamRef.current.declineFallback();
    setDraft((current) => current || question);
  }, []);
  const setMode = useCallback((nextMode: AskMode) => {
    if (streamRef.current.isActive) return;
    if (nextMode === 'deep' && !options.deepAvailable) return;
    setSelectedMode(nextMode);
    // A quota decision still belongs to the rejected request's snapshot.
    if (!streamRef.current.state.quota) streamRef.current.setMode(nextMode);
  }, [options.deepAvailable]);
  const refreshTurn = useCallback(async (turn: AskTurnView) => {
    if (!turn.askId) return;
    const generation = epoch.current;
    try {
      const result = await api.askById(turn.askId);
      if (generation !== epoch.current) return;
      setTurns((previous) => mergeHistory(previous, [result]));
      setReadError(null);
    } catch {
      if (generation === epoch.current) {
        setReadError(copy.ask.historyTurnError);
      }
    }
  }, []);

  return {
    turns, draft, mode, setDraft, setMode, submit, retry, newTopic,
    historyStatus, reloadHistory, startWithoutHistory, refreshTurn,
    isActive, hasUnread, contextCandidateIds, hasStartedNewTopic,
    online, canSubmit, readError,
    deepQuotaKnown: Boolean(deepBucket),
    deepUnlimited: Boolean(deepBucket
      && (deepBucket.limit === null || deepBucket.remaining === null)),
    stream: {
      ...stream,
      declineFallback,
      deepRemaining: deepBucket?.remaining ?? stream.deepRemaining,
    },
  };
}
