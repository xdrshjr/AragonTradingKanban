'use client';

import {
  DEFAULT_CHART_PREFERENCES,
  type ChartPreferences,
} from '@/lib/chartPreferences';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';

export interface ChartSettingsProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  preferences: ChartPreferences;
  onChange: (next: ChartPreferences) => void;
  canUseLog?: boolean;
  onInvalidLog?: () => void;
}

const MAIN_INDICATORS = [
  ['ma5', 'MA5', '5 根收盘均价'],
  ['ma10', 'MA10', '10 根收盘均价'],
  ['ma20', 'MA20', '20 根收盘均价'],
  ['ma60', 'MA60', '60 根收盘均价'],
  ['ema', 'EMA12 / EMA26', '12 / 26 根，SMA 作为初值'],
  ['boll', 'BOLL(20, 2)', '中轨 SMA20，上下轨 ±2 倍总体标准差'],
] as const;

const OSCILLATORS = [
  ['macd', 'MACD(12,26,9)', 'DIF、DEA、柱值 2×(DIF−DEA)，含零线'],
  ['rsi', 'RSI14', 'Wilder 平滑；全平为 50，参考线 30 / 70'],
  ['kdj', 'KDJ(9,3,3)', '初始 K / D 为 50，J 不截断；参考线 20 / 80'],
  ['atr', 'ATR14', 'Wilder 平滑，首根 TR=高−低；单位为价格币种'],
  ['none', '关闭振荡副图', '保留主图与成交量设置'],
] as const;

function MainSettings({ preferences, onChange }: ChartSettingsProps) {
  return (
    <fieldset className="chart-settings-group">
      <legend>主图叠加</legend>
      {MAIN_INDICATORS.map(([key, title, description]) => (
        <label key={key} className="chart-settings-option">
          <input
            type="checkbox"
            checked={preferences.main[key]}
            onChange={(event) => onChange({
              ...preferences,
              main: { ...preferences.main, [key]: event.target.checked },
            })}
          />
          <span><strong>{title}</strong><small>{description}</small></span>
        </label>
      ))}
    </fieldset>
  );
}

function OscillatorSettings({ preferences, onChange }: ChartSettingsProps) {
  return (
    <fieldset className="chart-settings-group">
      <legend>振荡副图（单选）</legend>
      {OSCILLATORS.map(([key, title, description]) => (
        <label key={key} className="chart-settings-option">
          <input
            type="radio"
            name="chart-oscillator"
            checked={preferences.oscillator === key}
            onChange={() => onChange({ ...preferences, oscillator: key })}
          />
          <span><strong>{title}</strong><small>{description}</small></span>
        </label>
      ))}
    </fieldset>
  );
}

function DisplaySettings(props: ChartSettingsProps) {
  const { preferences, onChange, canUseLog = true, onInvalidLog } = props;
  return (
    <fieldset className="chart-settings-group">
      <legend>显示方式</legend>
      <label className="chart-settings-option">
        <input
          type="checkbox" checked={preferences.volume}
          onChange={(event) => onChange({
            ...preferences, volume: event.target.checked,
          })}
        />
        <span><strong>独立成交量</strong><small>原始量单位及 MAVOL5 / 10</small></span>
      </label>
      <label className="chart-settings-select">图形
        <select
          value={preferences.style}
          onChange={(event) => onChange({
            ...preferences,
            style: event.target.value as ChartPreferences['style'],
          })}
        >
          <option value="candlestick">蜡烛图</option>
          <option value="line">收盘折线</option>
        </select>
      </label>
      {!canUseLog ? <small>
        当前价格包含非正数，无法使用对数坐标。
      </small> : null}
      <label className="chart-settings-select">主图坐标
        <select
          value={preferences.scale}
          onChange={(event) => {
            const scale = event.target.value as ChartPreferences['scale'];
            if (scale === 'log' && !canUseLog) return onInvalidLog?.();
            onChange({ ...preferences, scale });
          }}
        >
          <option value="linear">线性</option>
          <option value="log">对数</option>
        </select>
      </label>
    </fieldset>
  );
}

/** Applies each preference immediately; storage belongs to the stable owner. */
export function ChartSettings(props: ChartSettingsProps) {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent
        title="图表指标设置"
        description="固定参数，即改即生效；参考线不代表买卖信号。"
        className="chart-settings max-h-[90dvh] overflow-y-auto p-4"
      >
        <h2 className="mb-3 text-sm font-semibold">指标与图表</h2>
        <div className="chart-settings-grid">
          <MainSettings {...props} />
          <OscillatorSettings {...props} />
          <DisplaySettings {...props} />
        </div>
        <p className="my-3 text-xs text-[var(--text-tertiary)]">
          aragon-chart-v1：EMA 使用 SMA 初值。历史不足的指标保留空值，
          不补零。周线按周、月线按月计算。参考线不代表买卖信号。
        </p>
        <div className="flex justify-end gap-2">
          <Button onClick={() => props.onChange(DEFAULT_CHART_PREFERENCES)}>
            恢复默认
          </Button>
          <Button onClick={() => props.onOpenChange(false)}>关闭设置</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
