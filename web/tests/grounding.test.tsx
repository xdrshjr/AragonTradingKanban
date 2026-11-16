import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { BlockFootnote } from '@/components/report/BlockFootnote';
import { GroundingDot } from '@/components/report/GroundingDot';
import { MetricGrid } from '@/components/report/blocks/MetricsBlock';
import { TrustBadge } from '@/components/report/TrustBadge';
import { copy, fill } from '@/lib/copy';
import { blockSampleCount, indexSamples, jumpHref, sampleLabel, trustState, trustText } from '@/lib/grounding';
import type { GroundingQuality, GroundingSample } from '@/lib/types';

function sample(overrides: Partial<GroundingSample> = {}): GroundingSample {
  return {
    blockId: 'valuation#2',
    path: 'valuation.table.rows[2].pe',
    raw: '28.4',
    text: '可比公司平均 28.4×，略高于历史中位数',
    ...overrides,
  };
}

function makeQuality(overrides: Partial<GroundingQuality> = {}): GroundingQuality {
  return {
    factClaims: 51,
    factMissing: 0,
    totalMissing: 0,
    projectionClaims: 0,
    derivedClaims: 0,
    identifierClaims: 0,
    skipped: null,
    samples: [],
    ...overrides,
  };
}

/**
 * AC-50 / ★V6-P2-8：三态判定的**优先级**是这一层唯一的裁决点。
 *
 * 关键用例是"优先级专测"：`factClaims=3, factMissing=0, skipped='insufficient_sample'` 会
 * 同时命中"样本不足"和"已核验 3/3"，必须输出前者 —— 后者是虚假的安心。
 */
describe('lib/grounding.ts · trustState（AC-50 三态 + 优先级）', () => {
  it('没有 grounding 数据 → absent（而不是"0 个未接地"）', () => {
    expect(trustState(null).kind).toBe('absent');
    expect(trustState(undefined).kind).toBe('absent');
  });

  it('skipped="insufficient_sample" → insufficient，且**压过**事实上的 1.0', () => {
    const quality = makeQuality({ factClaims: 3, factMissing: 0, skipped: 'insufficient_sample' });
    expect(trustState(quality)).toEqual({ kind: 'insufficient', factClaims: 3 });
  });

  it('factMissing === 0 → verified，并带上 ok/total', () => {
    expect(trustState(makeQuality({ factClaims: 51, factMissing: 0 }))).toEqual({
      kind: 'verified',
      ok: 51,
      total: 51,
    });
  });

  it('跌破阈值 → missing，missing 与 total 都来自服务质量', () => {
    expect(trustState(makeQuality({ factClaims: 10, factMissing: 3, totalMissing: 3 }))).toEqual({
      kind: 'missing',
      missing: 3,
      total: 10,
    });
  });

  it('阈值与后端同源：0.9 是分界线（48/51=0.94 已核验，45/51=0.88 未达标）', () => {
    expect(trustState(makeQuality({ factClaims: 51, factMissing: 3 })).kind).toBe('verified');
    expect(trustState(makeQuality({ factClaims: 51, factMissing: 6 })).kind).toBe('missing');
  });

  it('trustText 只对 absent 返回 null；其余三态都有中文文案', () => {
    expect(trustText({ kind: 'absent' })).toBeNull();
    expect(trustText({ kind: 'insufficient', factClaims: 3 })).toBe(copy.trust.insufficient);
    expect(trustText({ kind: 'verified', ok: 48, total: 51 })).toBe(
      fill(copy.trust.verified, { ok: 48, total: 51 }),
    );
    expect(trustText({ kind: 'missing', missing: 3, total: 51 })).toBe(
      fill(copy.trust.missing, { count: 3 }),
    );
  });
});

describe('lib/grounding.ts · 样本索引与辅助', () => {
  it('indexSamples 按 blockId 分组，并对同一份 quality 只解析一次', () => {
    const quality = makeQuality({
      factMissing: 3,
      totalMissing: 3,
      samples: [
        sample({ blockId: 'valuation#2', raw: '28.4' }),
        sample({ blockId: 'thesis#0', raw: '15.2', path: 'thesis.bullets[1]' }),
        sample({ blockId: 'valuation#2', raw: '3.1', path: 'valuation.table.rows[3].pb' }),
      ],
    });

    const index = indexSamples(quality);
    expect(index.get('valuation#2')?.map((item) => item.raw)).toEqual(['28.4', '3.1']);
    expect(index.get('thesis#0')).toHaveLength(1);
    expect(index.get('nowhere#9')).toBeUndefined();

    // 缓存：同一对象拿到同一张表（组件每次渲染都查表，不能重新分组）。
    expect(indexSamples(quality)).toBe(index);
    // 没有载荷时是空表，不是 undefined。
    expect(indexSamples(null).size).toBe(0);
  });

  it('blockSampleCount 只数该块，空 blockId / 无载荷都返回 0', () => {
    const quality = makeQuality({
      samples: [sample({ blockId: 'a#0' }), sample({ blockId: 'a#0' }), sample({ blockId: 'b#1' })],
    });
    expect(blockSampleCount(quality, 'a#0')).toBe(2);
    expect(blockSampleCount(quality, 'b#1')).toBe(1);
    expect(blockSampleCount(quality, 'c#2')).toBe(0);
    expect(blockSampleCount(quality, '')).toBe(0);
    expect(blockSampleCount(null, 'a#0')).toBe(0);
  });

  it('jumpHref 复用路由的 ?report=&section=&block= 契约', () => {
    expect(jumpHref('rp_1', 'valuation#2')).toBe('?report=rp_1&section=valuation&block=valuation%232');
  });

  it('sampleLabel 同时给出原值与出处片段', () => {
    expect(sampleLabel(sample())).toBe(
      `${fill(copy.trust.sampleRaw, { raw: '28.4' })} · 可比公司平均 28.4×，略高于历史中位数`,
    );
    expect(sampleLabel(sample({ text: '' }))).toBe(fill(copy.trust.sampleRaw, { raw: '28.4' }));
  });
});

/**
 * AC-50：徽标三态 + "没有 grounding 数据时徽标不渲染"。
 */
describe('TrustBadge', () => {
  function badge() {
    return screen.getByTestId('trust-badge');
  }

  it('低于阈值 → 琥珀一行「N 个数字未找到依据」', () => {
    // 48/51 = 0.94 ≥ 0.9 会走「已核验」分支（AC-50 的第二个分支，也是 §19.3.2
    // 样例里那份报告）；要看到琥珀，必须真的跌破阈值。
    render(
      <TrustBadge
        quality={makeQuality({ factClaims: 10, factMissing: 3, totalMissing: 3, samples: [sample()] })}
        reportId="rp_1"
      />,
    );
    const button = badge();
    expect(button).toHaveTextContent(fill(copy.trust.missing, { count: 3 }));
    expect(button.className).toContain('warn');
    expect(button).toHaveAccessibleName(new RegExp(copy.trust.label));
    expect(button).toHaveAttribute('aria-expanded', 'false');
  });

  it('高于阈值但仍有未接地数字 → 「已核验 48/51」（AC-50 样例，不是琥珀）', () => {
    // 这条是阈值的**分界线**：3 个数字有问题不等于整份报告不可信；
    // 那 3 个数字仍然在展开面板与块脚注里逐条列出（不隐藏）。
    render(
      <TrustBadge
        quality={makeQuality({ factClaims: 51, factMissing: 3, totalMissing: 3, samples: [sample()] })}
        reportId="rp_1"
      />,
    );
    const button = badge();
    expect(button).toHaveTextContent(fill(copy.trust.verified, { ok: 48, total: 51 }));
    expect(button.className).not.toContain('warn-weak');
  });

  it('已核验 → 「已核验 51/51」，用上涨色而不是琥珀', () => {
    render(<TrustBadge quality={makeQuality({ factClaims: 51, factMissing: 0 })} reportId="rp_1" />);
    const button = badge();
    expect(button).toHaveTextContent(fill(copy.trust.verified, { ok: 51, total: 51 }));
    expect(button.className).toContain('chart-up');
    expect(button.className).not.toContain('warn-weak');
  });

  it('★优先级：skipped="insufficient_sample" 且 factMissing=0 → 说「数字太少，未评估」，绝不说「已核验」', () => {
    render(
      <TrustBadge
        quality={makeQuality({ factClaims: 3, factMissing: 0, skipped: 'insufficient_sample' })}
        reportId="rp_1"
      />,
    );
    const button = badge();
    expect(button).toHaveTextContent(copy.trust.insufficient);
    // 同一份数据完全满足 verified 分支（3/3），但优先级禁止它出现。
    expect(screen.queryByText(fill(copy.trust.verified, { ok: 3, total: 3 }))).toBeNull();
    expect(button).not.toHaveTextContent('已核验');
  });

  it('quality 为 null / undefined 时不渲染任何东西（不是空徽标）', () => {
    const { container: nullQuality } = render(<TrustBadge quality={null} reportId="rp_1" />);
    expect(nullQuality).toBeEmptyDOMElement();

    const { container: missingQuality } = render(<TrustBadge reportId="rp_1" />);
    expect(missingQuality).toBeEmptyDOMElement();
  });

  it('展开后列出样本（raw / blockId / text），点样本回调 onJump(blockId) 并带深链', async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    render(
      <TrustBadge
        quality={makeQuality({ factClaims: 51, factMissing: 3, totalMissing: 3, samples: [sample()] })}
        reportId="rp_1"
        onJump={onJump}
      />,
    );

    await user.click(badge());
    expect(badge()).toHaveAttribute('aria-expanded', 'true');

    const panel = screen.getByTestId('trust-badge-panel');
    expect(within(panel).getByText('28.4')).toBeInTheDocument();
    expect(within(panel).getByText('valuation#2')).toBeInTheDocument();
    expect(within(panel).getByText(sample().text)).toBeInTheDocument();

    const link = within(panel).getByRole('link', { name: /28\.4/ });
    expect(link).toHaveAttribute('data-href', jumpHref('rp_1', 'valuation#2'));
    await user.click(link);
    expect(onJump).toHaveBeenCalledWith('valuation#2');
  });

  it('样本被截断时交代「仅列前 N 个块，共 M 处」，并为 >0 的预测/标识符/推算值各留一行', async () => {
    const user = userEvent.setup();
    render(
      <TrustBadge
        quality={makeQuality({
          factClaims: 120,
          factMissing: 60,
          totalMissing: 60,
          projectionClaims: 24,
          identifierClaims: 18,
          derivedClaims: 2,
          samples: [sample()],
        })}
        reportId="rp_1"
      />,
    );

    await user.click(badge());
    const panel = screen.getByTestId('trust-badge-panel');
    expect(within(panel).getByText(fill(copy.trust.more, { shown: 1, total: 60 }))).toBeInTheDocument();
    expect(within(panel).getByText(fill(copy.trust.projections, { count: 24 }))).toBeInTheDocument();
    expect(within(panel).getByText(fill(copy.trust.identifiers, { count: 18 }))).toBeInTheDocument();
    expect(within(panel).getByText(fill(copy.trust.derived, { count: 2 }))).toBeInTheDocument();
    // 0 行不渲染：不然面板里会出现「另有 0 处…」这种废话。
    expect(within(panel).queryByText(fill(copy.trust.projections, { count: 0 }))).toBeNull();
  });

  it('已核验时展开只说「所有数字都能在快照里找到依据」，不声称可跳转', async () => {
    const user = userEvent.setup();
    render(<TrustBadge quality={makeQuality({ factClaims: 8, factMissing: 0 })} reportId="rp_1" />);
    await user.click(badge());
    const panel = screen.getByTestId('trust-badge-panel');
    expect(within(panel).getByText(copy.trust.none)).toBeInTheDocument();
    expect(within(panel).queryByRole('link')).toBeNull();
  });

  it('★I-51：ratio ≥ 0.9 但仍有未接地数字时，展开**不得**说「所有数字都能找到依据」', async () => {
    // `verified` 分支在 I-40 里被放宽成 `ratio >= 0.9`，所以 48/51 也算 `verified`。
    // 此时再补一句 `copy.trust.none` 就是自相矛盾的假话：徽标说「已核验 48/51」，
    // 展开面板说「所有数字都能在快照里找到依据」。修法是让这句话只在 0 处未接地时出现。
    const user = userEvent.setup();
    render(
      <TrustBadge
        quality={makeQuality({ factClaims: 51, factMissing: 3, totalMissing: 3, samples: [sample()] })}
        reportId="rp_1"
      />,
    );
    await user.click(badge());
    const panel = screen.getByTestId('trust-badge-panel');
    expect(within(panel).queryByText(copy.trust.none)).toBeNull();
    // 但"仅列前 N 个块"这类真实的交代仍然要在。
    expect(within(panel).getByText(fill(copy.trust.more, { shown: 1, total: 3 }))).toBeInTheDocument();
  });
});

/**
 * AC-49 / AC-36：点不是唯一信号 —— 两个状态都必须有整句 `aria-label`。
 */
describe('GroundingDot', () => {
  it('未接地的点带完整句子（含数值），并标记 data-grounded="false"', () => {
    render(<GroundingDot value="28.4" grounded={false} />);
    const dot = screen.getByRole('img', { name: fill(copy.trust.dotMissing, { value: '28.4' }) });
    expect(dot).toHaveAttribute('data-grounded', 'false');
    expect(dot.className).toContain('warn');
  });

  it('已接地的点仍然存在，只是文案与配色不同（"没有点"不等于"检查过"）', () => {
    render(<GroundingDot value="15.2%" grounded />);
    const dot = screen.getByRole('img', { name: fill(copy.trust.dotGrounded, { value: '15.2%' }) });
    expect(dot).toHaveAttribute('data-grounded', 'true');
    expect(dot.className).not.toContain('warn');
  });
});

/**
 * AC-49 / R-22：结构化组件只能**标注缺失**。
 *
 * `samples` 只有前 20 个块，所以"不在样本里"≠"已核验"。这条用例守的是反向断言：
 * 指标卡绝不允许给一个没有核验依据的数字盖「已在快照中找到依据」的章 ——
 * 那是替模型自证（与 V6-P1-1 删掉 `sourceRefs` 升格通道同型）。
 */
describe('MetricGrid · 绝不把未核验的数字标成已接地', () => {
  const items = [{ label: '最新价', value: '1,672.00' }, { label: '市盈率', value: '28.4' }];

  it('没有 grounding 数据 → 一个点都不渲染', () => {
    render(<MetricGrid items={items} blockId="keyMetrics" quality={null} />);
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('有 grounding 但该块不在样本里 → 仍然不渲染"已核验"的点', () => {
    const quality = makeQuality({ factClaims: 51, factMissing: 1, totalMissing: 1, samples: [sample()] });
    render(<MetricGrid items={items} blockId="keyMetrics" quality={quality} />);
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByText(fill(copy.trust.dotGrounded, { value: '28.4' }))).toBeNull();
  });

  it('该值真的在样本里 → 只给它挂一个"未找到依据"的点', () => {
    const quality = makeQuality({
      factClaims: 51,
      factMissing: 1,
      totalMissing: 1,
      samples: [sample({ blockId: 'keyMetrics', raw: '28.4', path: 'keyMetrics[1].value' })],
    });
    render(<MetricGrid items={items} blockId="keyMetrics" quality={quality} />);
    const dots = screen.getAllByRole('img');
    expect(dots).toHaveLength(1);
    expect(dots[0]).toHaveAccessibleName(fill(copy.trust.dotMissing, { value: '28.4' }));
    expect(dots[0]).toHaveAttribute('data-grounded', 'false');
  });
});

/**
 * AC-49：含 claim 的块下方要有一行脚注，且点样本能跳回该块；其余情况不渲染。
 */
describe('BlockFootnote', () => {
  it('该块有样本 → 一行细字 + 可展开的原值，点它回调 onJump(blockId)', async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    const quality = makeQuality({ factClaims: 51, factMissing: 1, totalMissing: 1, samples: [sample()] });

    render(<BlockFootnote quality={quality} blockId="valuation#2" onJump={onJump} />);
    expect(screen.getByText(fill(copy.trust.footnoteMissing, { count: 1, missing: 1 }))).toBeInTheDocument();

    const toggle = screen.getByRole('button', { name: copy.trust.expand });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);

    const list = screen.getByTestId('block-footnote-samples');
    expect(within(list).getByText('28.4')).toBeInTheDocument();
    await user.click(within(list).getByRole('link', { name: sampleLabel(sample()) }));
    expect(onJump).toHaveBeenCalledWith('valuation#2');
  });

  it('该块没有样本 / 没有 grounding → 完全不渲染', () => {
    const quality = makeQuality({ factMissing: 1, samples: [sample({ blockId: 'thesis#0' })] });
    const { container: otherBlock } = render(<BlockFootnote quality={quality} blockId="valuation#2" />);
    expect(otherBlock).toBeEmptyDOMElement();

    const { container: noQuality } = render(<BlockFootnote quality={null} blockId="valuation#2" />);
    expect(noQuality).toBeEmptyDOMElement();

    const { container: noSamples } = render(
      <BlockFootnote quality={makeQuality({ factClaims: 51 })} blockId="valuation#2" />,
    );
    expect(noSamples).toBeEmptyDOMElement();
  });

  it('调用方直接给样本 / 不给 onJump 时，原值退化为纯文本而不是一个点了没反应的按钮', async () => {
    const user = userEvent.setup();
    render(<BlockFootnote quality={makeQuality()} blockId="valuation#2" samples={[sample()]} />);
    expect(screen.getByText(fill(copy.trust.footnoteMissing, { count: 1, missing: 1 }))).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: copy.trust.expand }));
    const list = screen.getByTestId('block-footnote-samples');
    expect(within(list).queryByRole('link')).toBeNull();
    expect(within(list).getByText('28.4')).toBeInTheDocument();
  });
});
