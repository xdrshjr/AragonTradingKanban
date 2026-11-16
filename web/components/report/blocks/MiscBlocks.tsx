'use client';

import { cn } from '@/lib/cn';
import { num, pct } from '@/lib/format';
import type { Peer, Scenario, TimelineEvent } from '@/lib/types';

import { CitationChip, RichText } from '../CitationChip';

/* ── bullets ─────────────────────────────────────────────────────────────── */

export function BulletList({
  items,
  sources,
}: {
  items: { text: string; sourceRefs?: string[] }[];
  sources: { id: string }[];
}) {
  return (
    <ul className="my-0 space-y-1.5 pl-0">
      {items.map((item, index) => (
        <li key={index} className="relative pl-4 text-[15px] leading-[1.8] text-[var(--text-primary)]">
          <span
            aria-hidden="true"
            className="absolute left-0 top-[0.72em] size-1.5 rounded-full bg-[var(--border-strong)]"
          />
          <RichText text={item.text} sources={sources as never} />
          {item.sourceRefs?.map((ref) => (
            <CitationChip key={ref} sourceId={ref} />
          ))}
        </li>
      ))}
    </ul>
  );
}

/* ── quote ───────────────────────────────────────────────────────────────── */

export function QuoteCard({ text, attribution }: { text: string; attribution: string }) {
  return (
    <blockquote className="my-0 rounded-[var(--radius-md)] border-l-[3px] border-[var(--accent)] bg-[var(--bg-surface-1)] pl-3.5 pr-3 py-2.5">
      <p className="text-[14.5px] italic leading-relaxed text-[var(--text-primary)]">{text}</p>
      <footer className="mt-1 text-[11.5px] not-italic text-[var(--text-tertiary)]">— {attribution}</footer>
    </blockquote>
  );
}

/* ── timeline ────────────────────────────────────────────────────────────── */

export function Timeline({ events, sources }: { events: TimelineEvent[]; sources: { id: string }[] }) {
  return (
    <ol className="relative m-0 list-none space-y-3 border-l border-[var(--border-subtle)] pl-4">
      {events.map((event, index) => (
        <li key={`${event.date}-${index}`} className="relative">
          <span
            aria-hidden="true"
            className="absolute -left-[21px] top-[6px] size-[7px] rounded-full border border-[var(--bg-canvas)] bg-[var(--accent)]"
          />
          <TimeLabel date={event.date} />
          <p className="text-[13px] font-medium leading-snug text-[var(--text-primary)]">{event.title}</p>
          {event.detail ? (
            <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
              <RichText text={event.detail} sources={sources as never} />
            </p>
          ) : null}
          {event.sourceRefs?.map((ref) => (
            <CitationChip key={ref} sourceId={ref} className="!h-4 !min-w-[16px] !text-[9px]" />
          ))}
        </li>
      ))}
    </ol>
  );
}

function TimeLabel({ date }: { date: string }) {
  // Dates here may be `2025-08-14` or `2025Q3`; only real ISO dates jump to the chart.
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(date);
  if (!iso) {
    return <span className="mono text-[10.5px] tracking-wide text-[var(--accent)]">{date}</span>;
  }
  return <DateJump date={date} />;
}

function DateJump({ date }: { date: string }) {
  return (
    <span className="mono text-[10.5px] tracking-wide text-[var(--accent)]">
      <RichText text={date} />
    </span>
  );
}

/* ── comparables ─────────────────────────────────────────────────────────── */

export function Comparables({ peers }: { peers: Peer[] }) {
  const maxPe = Math.max(...peers.map((peer) => peer.pe ?? 0), 1);
  return (
    <div className="grid gap-1.5 sm:grid-cols-2">
      {peers.map((peer) => (
        <article
          key={peer.symbol}
          className="rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] p-2.5"
        >
          <header className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[12.5px] font-medium text-[var(--text-primary)]">{peer.name}</span>
            <span className="mono shrink-0 text-[10px] text-[var(--text-tertiary)]">{peer.symbol}</span>
          </header>
          <dl className="mt-1.5 grid grid-cols-3 gap-1.5 text-[11px]">
            <Stat label="PE" value={peer.pe === null || peer.pe === undefined ? '—' : `${num(peer.pe, 1)}×`} />
            <Stat label="PB" value={peer.pb === null || peer.pb === undefined ? '—' : `${num(peer.pb, 1)}×`} />
            <Stat
              label="增速"
              value={peer.growthPct === null || peer.growthPct === undefined ? '—' : pct(peer.growthPct)}
            />
          </dl>
          {peer.pe !== null && peer.pe !== undefined ? (
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--bg-surface-3)]" aria-hidden="true">
              <div
                className="h-full rounded-full bg-[var(--accent)]/60"
                style={{ width: `${Math.min(100, (peer.pe / maxPe) * 100)}%` }}
              />
            </div>
          ) : null}
          {peer.note ? <p className="mt-1.5 text-[10.5px] leading-snug text-[var(--text-tertiary)]">{peer.note}</p> : null}
        </article>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[9.5px] uppercase tracking-wide text-[var(--text-tertiary)]">{label}</dt>
      <dd className="num text-[12px] font-medium text-[var(--text-secondary)]">{value}</dd>
    </div>
  );
}

/* ── scenario ────────────────────────────────────────────────────────────── */

const SCENARIO_TONE: Record<string, { label: string; bar: string; text: string }> = {
  bear: { label: '悲观', bar: 'bg-[var(--chart-down)]', text: 'text-[var(--chart-down)]' },
  base: { label: '中性', bar: 'bg-[var(--accent)]', text: 'text-[var(--accent)]' },
  bull: { label: '乐观', bar: 'bg-[var(--chart-up)]', text: 'text-[var(--chart-up)]' },
};

export function ScenarioTable({ scenarios }: { scenarios: Scenario[] }) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)]">
      <div className="grid grid-cols-3 gap-px bg-[var(--border-subtle)]">
        {scenarios.map((scenario) => {
          const tone = SCENARIO_TONE[scenario.name] ?? SCENARIO_TONE.base!;
          return (
            <div key={scenario.name} className="flex flex-col gap-1 bg-[var(--bg-surface-1)] p-2.5">
              <div className="flex items-baseline justify-between">
                <span className={cn('text-[11px] font-medium', tone.text)}>{tone.label}</span>
                <span className="num text-[10.5px] text-[var(--text-tertiary)]">
                  {(scenario.probability * 100).toFixed(0)}%
                </span>
              </div>
              <span className="num text-[16px] font-semibold tracking-tight text-[var(--text-primary)]">
                {num(scenario.targetPrice, 2)}
              </span>
              <div className="h-[3px] overflow-hidden rounded-full bg-[var(--bg-surface-3)]" aria-hidden="true">
                <div className={cn('h-full rounded-full', tone.bar)} style={{ width: `${scenario.probability * 100}%` }} />
              </div>
              <p className="mt-0.5 text-[10.5px] leading-snug text-[var(--text-tertiary)]">{scenario.assumptions}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
