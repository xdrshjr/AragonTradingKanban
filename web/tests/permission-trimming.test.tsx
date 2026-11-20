import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RoleBadge } from '@/components/auth/RoleBadge';
import { LibraryFilters } from '@/components/library/LibraryFilters';
import { ReportLibraryRow } from '@/components/library/ReportLibraryRow';
import { VersionSwitcher } from '@/components/report/VersionSwitcher';
import { CommandPalette, type CommandPaletteProps } from '@/components/shell/CommandPalette';
import { RunStatusBadge } from '@/components/shell/RunStatusBadge';
import { SymbolRail } from '@/components/shell/SymbolRail';
import { initialGenerationState } from '@/components/generation/useGenerationStream';
import { useUiStore } from '@/lib/store';
import type { Instrument, ReportLibraryItem, ReportSummary } from '@/lib/types';

import { renderWithMe } from './authHarness';

/**
 * §24.4.2：入口按权限裁剪（体验层）。服务端才是授权依据；这里证明
 * “必然 401/403 的入口不会被摆出来”，并且**未知（旧后端 / 开放模式）不收窄**。
 */

const INSTRUMENT: Instrument = {
  id: 'CN:600519.SH',
  symbol: '600519.SH',
  name: '贵州茅台',
  market: 'CN',
  currency: 'CNY',
  exchange: 'SSE',
  sector: '食品饮料',
  tz: 'Asia/Shanghai',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
};

function summary(overrides: Partial<ReportSummary> = {}): ReportSummary {
  return {
    id: 'r1',
    instrumentId: INSTRUMENT.id,
    version: 2,
    status: 'ready',
    title: 'T',
    headline: 'H',
    asOf: '2026-11-01',
    generatedAt: '2026-11-01T00:00:00Z',
    isLatest: false,
    pinned: false,
    archived: false,
    provisional: false,
    quality: { source: 'agent' } as unknown as ReportSummary['quality'],
    costKnown: false,
    docBytes: 1,
    ...overrides,
  };
}

function libraryItem(report: Partial<ReportSummary>): ReportLibraryItem {
  return {
    report: summary(report),
    instrument: { id: INSTRUMENT.id, symbol: INSTRUMENT.symbol, name: INSTRUMENT.name } as ReportLibraryItem['instrument'],
    integrity: 'ok',
  };
}

describe('SymbolRail：游客的自选只读', () => {
  const props = {
    instruments: [{ instrument: INSTRUMENT }],
    watchlist: [{ instrument: INSTRUMENT, sortOrder: 0, pinned: false, addedAt: '2026-01-01T00:00:00Z' }],
    selectedId: null,
    onSelect: vi.fn(),
    onResolved: vi.fn(),
    onToggleWatchlist: vi.fn(),
  };

  it('readOnlyWatchlist：没有“移出自选”按钮、分组叫“平台精选”、有“登录后可自定义自选”', () => {
    render(<SymbolRail {...props} readOnlyWatchlist />);
    expect(screen.queryByRole('button', { name: '移出自选' })).toBeNull();
    expect(screen.queryByRole('button', { name: '加入自选' })).toBeNull();
    expect(screen.getByText('平台精选')).toBeInTheDocument();
    expect(screen.getByText('登录后可自定义自选')).toBeInTheDocument();
  });

  it('缺省（可编辑）按旧行为：有写操作按钮，没有提示', () => {
    render(<SymbolRail {...props} />);
    expect(screen.getByRole('button', { name: '移出自选' })).toBeInTheDocument();
    expect(screen.queryByText('登录后可自定义自选')).toBeNull();
  });
});

describe('VersionSwitcher：置顶 / 归档 / 删除按 canCurate / canManage（RV-10）', () => {
  async function open(report: ReportSummary) {
    // 列表里的每一行都要带同样的视角标记，否则“最新版”那一行会因字段缺席而按“未知 ⇒ 不收窄”出按钮。
    const flags = { canManage: report.canManage, canCurate: report.canCurate };
    const view = render(
      <VersionSwitcher reports={[summary({ id: 'latest', isLatest: true, version: 3, ...flags }), report]} activeId="latest" onSelect={() => {}} onPin={() => {}} onArchive={() => {}} onDelete={() => {}} />,
    );
    await userEvent.click(screen.getByRole('button', { expanded: false }));
    return view;
  }

  it('普通用户的自己的版本：能归档 / 删除，但不能置顶（全站可见旗标只给管理员）', async () => {
    await open(summary({ canManage: true, canCurate: false }));
    expect(screen.getAllByRole('button', { name: /归档/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '删除该版本' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /置顶/ })).toBeNull();
  });

  it('别人的版本（canManage=false）：只读，没有任何管理按钮', async () => {
    await open(summary({ canManage: false, canCurate: false }));
    expect(screen.queryByRole('button', { name: '删除该版本' })).toBeNull();
    expect(screen.queryByRole('button', { name: /置顶/ })).toBeNull();
  });

  it('字段缺席（旧后端 / 开放模式）：不收窄，按钮照旧', async () => {
    await open(summary());
    expect(screen.getByRole('button', { name: '删除该版本' })).toBeInTheDocument();
  });
});

describe('报告库：行内操作与标签', () => {
  it('canCurate=false：没有星标按钮，已置顶只读展示；canManage=false：回收站没有“恢复”', () => {
    const item = libraryItem({ pinned: true });
    const { rerender } = render(<ReportLibraryRow item={item} canCurate={false} />);
    expect(screen.queryByRole('button', { name: /星标/ })).toBeNull();
    expect(screen.getByRole('img', { name: '已置顶' })).toBeInTheDocument();

    rerender(<ReportLibraryRow item={{ ...item, deletedAt: '2026-11-01T00:00:00Z' }} trash canManage={false} />);
    expect(screen.queryByRole('button', { name: /恢复/ })).toBeNull();
    rerender(<ReportLibraryRow item={{ ...item, deletedAt: '2026-11-01T00:00:00Z' }} trash />);
    expect(screen.getByRole('button', { name: /恢复/ })).toBeInTheDocument();
  });

  it('LibraryFilters：游客（showTrash=false）没有“回收站”标签', () => {
    const base = { scope: 'all' as const, counts: {}, q: '', total: 0, onScopeChange: () => {}, onQueryChange: () => {} };
    const { rerender } = render(<LibraryFilters {...base} />);
    expect(screen.getByRole('tab', { name: /回收站/ })).toBeInTheDocument();
    rerender(<LibraryFilters {...base} showTrash={false} />);
    expect(screen.queryByRole('tab', { name: /回收站/ })).toBeNull();
    expect(screen.getAllByRole('tab').length).toBe(3);
  });
});

describe('RunStatusBadge：端点徽标只给 engine.probe', () => {
  const generation = { state: initialGenerationState, isActive: false, start: vi.fn(), cancel: vi.fn(), reset: vi.fn() } as never;

  it('canProbe=false：只有“引擎已就绪”一个状态点，没有 provider/model@host', () => {
    render(
      <RunStatusBadge
        generation={generation}
        agentMode="real"
        canProbe={false}
        aragon={{ provider: 'p', model: 'm', baseUrlHost: 'h.example' } as never}
      />,
    );
    expect(screen.getByText('引擎已就绪')).toBeInTheDocument();
    expect(screen.queryByText(/h\.example/)).toBeNull();
  });

  it('示例模式对所有人可见（它是事实，不是运维细节）', () => {
    render(<RunStatusBadge generation={generation} agentMode="mock" canProbe={false} />);
    expect(screen.getByText(/示例模式/)).toBeInTheDocument();
  });
});

describe('CommandPalette：账号命令按权限出现（“不传即不出现”）', () => {
  function props(overrides: Partial<CommandPaletteProps> = {}): CommandPaletteProps {
    return {
      instruments: [],
      reports: [],
      onSelectInstrument: vi.fn(),
      onSelectVersion: vi.fn(),
      onGenerate: vi.fn(),
      onExport: vi.fn(),
      onToggleChart: vi.fn(),
      ...overrides,
    };
  }
  beforeEach(() => useUiStore.setState({ commandPaletteOpen: true }));

  it('游客只有「登录或注册」', () => {
    render(<CommandPalette {...props({ onSignIn: vi.fn() })} />);
    expect(screen.getByText('登录或注册')).toBeInTheDocument();
    expect(screen.queryByText('账号与额度')).toBeNull();
    expect(screen.queryByText('退出登录')).toBeNull();
    expect(screen.queryByText('管理后台')).toBeNull();
  });

  it('用户：账号与额度 + 退出登录；管理员另有管理后台；点击收起面板', async () => {
    const onOpenAdmin = vi.fn();
    const onSignOut = vi.fn();
    render(<CommandPalette {...props({ onOpenAccount: vi.fn(), onOpenAdmin, onSignOut })} />);
    expect(screen.getByText('账号与额度')).toBeInTheDocument();
    await userEvent.click(screen.getByText('管理后台'));
    expect(onOpenAdmin).toHaveBeenCalledTimes(1);
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
  });

  it('什么都不传（开放模式）：三项都不出现', () => {
    render(<CommandPalette {...props()} />);
    for (const label of ['登录或注册', '账号与额度', '管理后台', '退出登录']) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });
});

describe('RoleBadge 在 renderWithMe 里不依赖身份', () => {
  it('渲染', () => {
    const { container } = renderWithMe(<RoleBadge role="admin" />);
    expect(container).toHaveTextContent('管理员');
  });
});
