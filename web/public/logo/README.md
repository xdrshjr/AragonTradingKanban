# 产品 LOGO

「Aragon Research Terminal · 投研报告工作台」的品牌标，**4 个纯图形（无字母/字形）概念**。
统一规范：深色画布 `#0b0c0e` · 主强调色 `#3ecf8e` · 辅助色 `#4c9aff` · 正文 `#edeef0`，与 `web/app/globals.css` 的设计 token 一致。

| 文件 | 概念 | 尺寸 | 适用场景 |
| --- | --- | --- | --- |
| `logo-05-layers.png` | **图层堆栈** — 三片圆角方板 45° 错位叠成透视堆栈，顶面一条上行折线 | 1254 × 1254 | 主视觉 / 分享图 / 启动页 |
| `logo-06-loop-trend.png` | **循环外溢** — 渐细开口圆环，缺口处引出上行折线收于圆点 | 1254 × 1254 | 表达「一次生成、反复查看」 |
| `logo-07-hex-facet.png` | **棱镜切面** — 正六边形被斜线切成两片错位，断层露蓝色细亮边 | 1254 × 1254 | 强图形感，做头像/应用图标 |
| `logo-08-terminal-rings.png` | **终端刻度盘** — 细线圆环 + 12 刻度 + 环内递增绿柱 | 1254 × 1254 | 投研属性最直白，缩到 32px 仍清楚 |

四张图标均为抽象几何，**不含任何字母、数字或字形**；图标下方 `Aragon Research Terminal` 是品牌字标文字，不属于图标。

## 备注

- 全部为 PNG 原图（每张约 1.2 MB）。用于网页时建议先降采样，例如出 512 / 128 / 32 三档。
- 生成方式：codex CLI 内置 `image_gen`（ChatGPT 账号鉴权，**无需 `OPENAI_API_KEY`**），底层即 gpt-image 系列。要指定模型，直接在提示词里写明「使用 gpt-image 生图」即可，不必配 API Key。
- 只有需要 `quality` / `size` / 透明底这类**参数级**控制时，才走 CLI 回退路径 `scripts/image_gen.py`（该路径要求 `OPENAI_API_KEY`）。
- 提示词留在 `.agentmesh/scratch/logo-gen2/prompt.txt`，微调时改那里重跑。
- 早先的 4 版字形标（字母 A 系列）已归档在 `.agentmesh/scratch/logo-gen/archive-letterforms/`，未放进 `public/`。
- 当前模型不支持 `background=transparent`，全部为实底深色；要透明版需改用 `gpt-image-1.5` 并先确认。
