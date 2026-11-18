import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { EndpointBadge, endpointState } from '@/components/shell/EndpointBadge';
import { TrustBadge } from '@/components/report/TrustBadge';
import { copy, fill } from '@/lib/copy';
import type { AragonCapability, QualitySalvage, GroundingQuality } from '@/lib/types';

/**
 * ★v12（S1 / S3 / S4 / AC-112）运行态能力的界面落点。
 *
 * 这一层要守住三件"能力/可信度必须写在脸上"的事：
 * 1. **进程级事实**（`loopOk` / `degradedReason=event_loop_unsupported`）——即使端点完全正常，
 *    起不了子进程就一次生成都做不成，所以它必须成为徽标的第四态，而不是并进「凭证失败」。
 * 2. **格式收敛**（`quality.clamp`）——"我们截短了模型的话"是**我们做过的事**，读者有权知道。
 * 3. **正文还原**（`quality.salvage.how`）——`json_repaired` 意味着括号是**我们**补的，更要写在脸上。
 *
 * 全部断言只看用户能读到的字（`data-state` / 可见文案 / 展开面板），不测内部状态：
 * 这三件事的价值就是"用户能看见"，而不是"字段传到了"。
 */

/** 一个完全健康的 aragon 能力载荷（各用例只覆盖关心的那几个字段）。 */
function makeAragon(overrides: Partial<AragonCapability> = {}): AragonCapability {
  return {
    available: true,
    version: '0.9.1',
    versionOk: true,
    provider: 'openai',
    model: 'gpt-5',
    hasApiKey: true,
    ajv: true,
    canStructured: true,
    hasSubmitResult: true,
    extraAllowTools: [],
    errors: [],
    ...overrides,
  };
}

/** 当前判据口径（镜像后端 `numeric_claims.RULES_VERSION`），用于让徽标进入可比状态。 */
const RULES = 'claims-v3';

function makeQuality(overrides: Partial<GroundingQuality> = {}): GroundingQuality {
  return {
    factClaims: 51,
    factMissing: 0,
    totalMissing: 0,
    distinctMissing: 0,
    distinctClaims: 51,
    sampledBlocks: 0,
    sampledValues: 0,
    projectionClaims: 0,
    derivedClaims: 0,
    identifierClaims: 0,
    skipped: null,
    samples: [],
    rules: RULES,
    docHash: 'doc-hash',
    snapshotHash: 'snap-hash',
    ...overrides,
  };
}

function makeSalvage(overrides: Partial<QualitySalvage> = {}): QualitySalvage {
  return {
    used: true,
    droppedKeys: 0,
    droppedSections: 0,
    droppedBlocks: 0,
    reasons: [],
    how: 'json_repaired',
    ...overrides,
  };
}

/** 展开徽标并返回面板（`TrustBadge` 的展开器是按钮本身，`aria-expanded` 是唯一开关）。 */
async function openTrustPanel(user: ReturnType<typeof userEvent.setup>, container?: HTMLElement) {
  const scope = container ? within(container) : screen;
  const button = scope.getByTestId('trust-badge');
  await user.click(button);
  expect(button).toHaveAttribute('aria-expanded', 'true');
  return scope.getByTestId('trust-badge-panel');
}

/**
 * 端点徽标的第四态：`loop_blocked` 回答的不是"端点通不通"，而是"这个进程能不能 spawn 子进程"。
 */
describe('EndpointBadge · ★v12 第四态 loop_blocked', () => {
  it('loopOk=false 时即使 verified=true 也判 loop_blocked —— 循环闸门优先于"端点通不通"', () => {
    // 为什么：这三个旧态都是"钥匙/端点"问题；而 Windows 上带 `--reload` 起的进程 spawn 不了
    // 子进程（E-88/E-89），此时再正确的凭证也做不成任何一次生成。判据若让 `verified` 先返回，
    // 用户会看到一枚绿色的「已验证」徽标，然后每次生成都失败，且无从解释。
    const blocked = endpointState(makeAragon({ verified: true, loopOk: false, verifiedAt: '2026-01-01T00:00:00Z' }));
    expect(blocked).toBe('loop_blocked');

    // 对照组：同一个端点、同一个 verified，只要 loopOk 不是 false（这里是缺省），就是 verified。
    // 这两行合起来才证明"是循环闸门在赢"，而不是别的字段顺手返回了 loop_blocked。
    expect(endpointState(makeAragon({ verified: true }))).toBe('verified');
    // 而且它**不许**退化成「凭证失败」：那会让用户去查一个没坏的东西（copy 注释里的红线）。
    expect(blocked).not.toBe('failed');
  });

  it('渲染层：loopReason 进 title、可见文案是「事件循环不支持」，绝不说「凭证失败」', () => {
    // 为什么测渲染而不只测判据：`endpointState()` 只给状态码，而用户读到的是文案。
    // 一旦组件里两处分支不同步，就会出现 `data-state="loop_blocked"` 配着「凭证失败」的
    // 自相矛盾画面——所以状态属性与可见字必须一起断言。
    const loopReason = 'event_loop_selector:_WindowsSelectorEventLoop';
    render(<EndpointBadge aragon={makeAragon({ verified: true, loopOk: false, loopReason })} />);

    const badge = screen.getByTestId('endpoint-badge');
    expect(badge).toHaveAttribute('data-state', 'loop_blocked');
    expect(badge).toHaveTextContent(copy.engine.loopBlocked);
    // 可操作方向相反的那句话不许出现：这里的下一步是"去掉 --reload 重启"，不是"去换 key"。
    expect(badge).not.toHaveTextContent(copy.engine.failed);
    // title 把「为什么」和「哪一行代码」都留给愿意悬停的人（诊断前缀原样回显）。
    expect(badge).toHaveAttribute(
      'title',
      `${copy.capabilities.reason.event_loop_unsupported} · ${loopReason}`,
    );
  });

  it('只有 degradedReason="event_loop_unsupported"（没有 loopOk 字段）时也是 loop_blocked —— 两个 wire 字段是同一事实的两个消费点', () => {
    // 为什么：`aragon.loopOk` 与 `aragon.degradedReason` 都派生自后端唯一的
    // `preflight.spawn_capability()`，一条走 capability、一条走降级原因。老载荷 / 单个消费者
    // 只带其中一个键时，判据若只认 `loopOk === false` 就会漏判——同一件事出现两种界面说法。
    const derived = makeAragon({ verified: true, degradedReason: 'event_loop_unsupported' });
    expect(derived.loopOk).toBeUndefined();
    expect(endpointState(derived)).toBe('loop_blocked');

    render(<EndpointBadge aragon={derived} />);
    const badge = screen.getByTestId('endpoint-badge');
    expect(badge).toHaveAttribute('data-state', 'loop_blocked');
    expect(badge).toHaveTextContent(copy.engine.loopBlocked);
    // 没有 loopReason 时 title 只有原因那句话，不许拼出一个悬空的 ` · `。
    expect(badge).toHaveAttribute('title', copy.capabilities.reason.event_loop_unsupported);
  });
});

/**
 * `TrustBadge` 的两个新行：格式收敛（clamp）与正文还原（salvage）。
 * 二者都在"降级抽屉"里——和"未接地 N 条"同一个地方，因为它们同样降低这份报告的可信度。
 */
describe('TrustBadge · ★v12 clamp / salvage 说明行', () => {
  it('clamp 有计数时展开能看到被截短的字段名；clamp=null 时那一行不存在（不回填、不猜）', async () => {
    // 为什么：clamp 说的是"我们做过的事"（按句界截短了模型的话），不是模型的错。
    // 但读者必须能去那一节复核，所以文案里要带**字段名**；而老报告没有这个键时
    // 一句"这份报告有 0 处被截短"就是凭空断言 —— 所以 null 时整行不出现。
    const user = userEvent.setup();
    const withClamp = render(
      <TrustBadge
        quality={makeQuality({ factClaims: 51, factMissing: 0 })}
        reportId="rp_1"
        clamp={{ count: 2, fields: ['summary', 'heading.text'] }}
      />,
    );

    const panel = await openTrustPanel(user, withClamp.container);
    const note = within(panel).getByText(/超过格式上限/);
    expect(note).toBeInTheDocument();
    expect(note).toHaveTextContent('2');
    expect(note).toHaveTextContent('summary');
    expect(note).toHaveTextContent('heading.text');

    // 同一个组件、同一份 quality，只把 clamp 换成 null：那一行必须消失。
    withClamp.unmount();
    const withoutClamp = render(
      <TrustBadge quality={makeQuality({ factClaims: 51, factMissing: 0 })} reportId="rp_1" clamp={null} />,
    );
    const barePanel = await openTrustPanel(user, withoutClamp.container);
    expect(within(barePanel).queryByText(/超过格式上限/)).toBeNull();
    // 面板本身仍然在（口径行等其他说明还在），所以"为空"不是靠整块没渲染糊弄过去的。
    expect(within(barePanel).getByText(fill(copy.trust.rules, { rules: RULES }))).toBeInTheDocument();
  });

  it('salvage.how="json_repaired" 时展开看到「补齐括号」——告诉读者这份 JSON 是捡回来的', async () => {
    // 为什么：`json_repaired` 意味着结构结果不是模型用 `submit_result` 交上来的，
    // 而是我们从正文里捞回来、并且**括号是我们补的**。这与"零问题"是相反的信息，
    // 所以必须出现在降级抽屉里，而不是写进一个前端读不到的地方（V11-P2-2）。
    const user = userEvent.setup();
    const salvaged = render(
      <TrustBadge
        quality={makeQuality({ factClaims: 51, factMissing: 0 })}
        reportId="rp_1"
        salvage={makeSalvage({ how: 'json_repaired' })}
      />,
    );

    const panel = await openTrustPanel(user, salvaged.container);
    const note = within(panel).getByText(/从模型正文/);
    expect(note).toBeInTheDocument();
    // 关键三个字：括号是补的，不是模型给的。
    expect(note).toHaveTextContent('补齐括号');
    // 它是"结构结果缺失"的记录，不是一句无害的补充说明。
    expect(note).toHaveTextContent('结构结果缺失');

    // 对照：没有 salvage 时这句话不许出现（老报告不回填）。
    salvaged.unmount();
    const bare = render(
      <TrustBadge quality={makeQuality({ factClaims: 51, factMissing: 0 })} reportId="rp_1" salvage={null} />,
    );
    const barePanel = await openTrustPanel(user, bare.container);
    expect(within(barePanel).queryByText(/结构结果缺失/)).toBeNull();
    expect(within(barePanel).queryByText(/补齐括号/)).toBeNull();
  });
});
