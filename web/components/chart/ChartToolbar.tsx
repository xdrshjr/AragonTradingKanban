'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';

import type { ChartTool } from '@/lib/chartDataset';
import type { ChartView } from '@/lib/types';

import { Segmented } from '../ui/Tabs';

export interface ChartToolbarProps {
  view: ChartView;
  onViewChange: (next: ChartView) => void;
  tool: ChartTool;
  onToolChange: (next: ChartTool) => void;
  disabled?: boolean;
  rangeQueryEnabled?: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onLatest: () => void;
  onLocate: (date: string) => void;
  onSettings: () => void;
  onExpand: () => void;
  onToggleTable: () => void;
  onCsv: () => void;
  onPng: () => void;
  onRefresh: () => void;
  expanded?: boolean;
  isRefreshing?: boolean;
  tableOpen?: boolean;
  exporting?: boolean;
}

const TIMEFRAMES = [
  { value: '1d', label: '日' },
  { value: '1w', label: '周' },
  { value: '1m', label: '月' },
] as const;
const RANGES = [
  { value: '1m', label: '1月' },
  { value: '3m', label: '3月' },
  { value: '6m', label: '6月' },
  { value: '1y', label: '1年' },
  { value: '3y', label: '3年' },
] as const;
const ADJUSTS = [
  { value: 'qfq', label: '前复权' },
  { value: 'none', label: '不复权' },
  { value: 'hfq', label: '后复权' },
] as const;

function ActionButton({
  children, onClick, disabled, pressed, title,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  pressed?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button" className="chart-tool-button" onClick={onClick}
      disabled={disabled} aria-pressed={pressed} title={title}
    >
      {children}
    </button>
  );
}

function WindowControls({ view, onViewChange }: ChartToolbarProps) {
  return (
    <>
      <Segmented
        options={RANGES} value={view.range} ariaLabel="图表区间"
        onChange={(range) => onViewChange({ ...view, range })}
      />
      <Segmented
        options={ADJUSTS} value={view.adjust} ariaLabel="复权方式"
        onChange={(adjust) => onViewChange({ ...view, adjust })}
      />
    </>
  );
}

function ToolControls(props: ChartToolbarProps) {
  const { tool, disabled, rangeQueryEnabled, onToolChange } = props;
  return (
    <div className="chart-toolbar-group" role="group" aria-label="图表工具">
      <ActionButton
        onClick={() => onToolChange('pan')} disabled={disabled}
        pressed={tool === 'pan'}
      >平移</ActionButton>
      <ActionButton
        onClick={() => onToolChange('report-range')}
        disabled={disabled || !rangeQueryEnabled}
        pressed={tool === 'report-range'}
        title={rangeQueryEnabled ? '拖动选择报告区间（或按住 Shift）'
          : '当前没有可反查报告，或服务未启用区间反查'}
      >报告框选</ActionButton>
      <ActionButton
        onClick={() => onToolChange('measure')} disabled={disabled}
        pressed={tool === 'measure'}
      >测量</ActionButton>
    </div>
  );
}

function ViewControls(props: ChartToolbarProps) {
  return (
    <div className="chart-toolbar-group" role="group" aria-label="图表视区">
      <ActionButton onClick={props.onZoomIn} disabled={props.disabled}>
        放大
      </ActionButton>
      <ActionButton onClick={props.onZoomOut} disabled={props.disabled}>
        缩小
      </ActionButton>
      <ActionButton onClick={props.onFit} disabled={props.disabled}>
        适配
      </ActionButton>
      <ActionButton onClick={props.onLatest} disabled={props.disabled}>
        最新
      </ActionButton>
    </div>
  );
}

function LocateControl(props: ChartToolbarProps) {
  const [date, setDate] = useState('');
  return (
    <form
      className="chart-toolbar-group"
      onSubmit={(event) => {
        event.preventDefault();
        if (date) props.onLocate(date);
      }}
    >
      <label className="chart-date-label">定位日期
        <input
          type="date" value={date} disabled={props.disabled}
          onChange={(event) => setDate(event.target.value)}
        />
      </label>
      <button
        type="submit" className="chart-tool-button"
        disabled={props.disabled || !date}
      >定位</button>
    </form>
  );
}

function MoreControls(props: ChartToolbarProps) {
  return (
    <>
      <ActionButton onClick={props.onExpand}>
        {props.expanded ? '收起扩展' : '扩展'}
      </ActionButton>
      <ActionButton
        onClick={props.onToggleTable} disabled={props.disabled}
        pressed={props.tableOpen}
      >数据表</ActionButton>
      <ActionButton
        onClick={props.onCsv} disabled={props.disabled || props.exporting}
      >下载 CSV</ActionButton>
      <ActionButton
        onClick={props.onPng} disabled={props.disabled || props.exporting}
        title="导出当前图形与图例；不含工具栏、框选及测量卡片"
      >下载 PNG</ActionButton>
      <ActionButton
        onClick={props.onRefresh} disabled={props.isRefreshing}
      >{props.isRefreshing ? '刷新中…' : '刷新行情'}</ActionButton>
    </>
  );
}

/** Keeps the toolbar compact using its actual pane width. */
export function ChartToolbar(props: ChartToolbarProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [isWide, setIsWide] = useState(false);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const measure = () => setIsWide(host.clientWidth >= 560);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={hostRef} className="chart-toolbar">
      <div className="chart-toolbar-primary">
        <Segmented
          options={TIMEFRAMES} value={props.view.timeframe}
          ariaLabel="K 线周期"
          onChange={(timeframe) => props.onViewChange({
            ...props.view, timeframe,
          })}
        />
        <ActionButton onClick={props.onSettings}>指标</ActionButton>
        {isWide ? <WindowControls {...props} /> : null}
      </div>
      {isWide ? <div className="chart-toolbar-actions">
        <ToolControls {...props} /><ViewControls {...props} />
        <LocateControl {...props} /><MoreControls {...props} />
      </div> : <details className="chart-toolbar-more">
        <summary className="chart-tool-button">更多</summary>
        <div className="chart-toolbar-actions">
          <WindowControls {...props} /><ToolControls {...props} />
          <ViewControls {...props} /><LocateControl {...props} />
          <MoreControls {...props} />
        </div>
      </details>}
    </div>
  );
}
