# InvestRing 前端视觉规范（visual-spec）

> 轻量活规范（issue #127）：**代码是唯一事实来源**——语义 token 见 `frontend/src/app/globals.css`，图表色板见 `frontend/src/lib/colors.ts`，状态映射见 Badge variant 与 `getStatusBadgeVariant()`。本文只记录**读代码拿不到的决策与理由**，以及跨文件的一致性约定。配色色值即「配色方案 v1」，任何改动须先改本文与 token，再改组件。

---

## 1. 色彩语义

### 1.1 核心决策（拍板项，勿推翻）

1. **红绿专属涨跌（红涨绿跌，中国市场惯例）**。`text-gain` / `text-loss` 及其 `-soft` / `-foreground` 变体**只允许**由 `getReturnColorClass` / `getReturnBgClass`（`lib/utils.ts`）或显式的涨跌语义输出；**badge 状态色永远不许用 gain/loss token，反之亦然**。涨跌另有 `+/-` 符号兜底（`formatReturnRate`/`formatPercent` 自带符号），颜色不是唯一信息通道（WCAG 1.4.1）。**资金流向不属涨跌语义**——现金扣款/到账等流入流出禁用 gain/loss token，金额用 `text-foreground` + `+/-` 符号表意（见 §8 结对行）。
2. **双红分离**——「涨红」与「异常红」是两种不同的红：

   | | 涨红 gain | 异常红 destructive |
   |---|---|---|
   | 色相 | 朱红 5°（偏橙、暖） | 绛红 348°（偏品红、冷） |
   | 使用边界 | 只用于**裸数值/图标**（涨跌幅、盈亏额） | 只用于**带文字标签的载体**（badge、按钮、错误提示），永不单独给数字着色 |

   二者从不出现在同一上下文；绛红永远伴随「失败/异常」文字，不靠颜色单独表意。
3. **success ≠ 绿**。绿色被「跌」独占后，靛蓝 `#2B5CD7` 兼任品牌主色 primary（链接、主按钮、focus 环同源）。**状态徽章降噪（#944 D3·S6）**：最高频的正常完成/活跃/终态（confirmed/active/success/passed/启用/cancelled/closed/draft/停用）一律 **neutral 灰**，让默认观感「一切正常」最不抢色；靛蓝软底（`success` variant）改留给**正常等待**（pending/running/在途），赭珀 `warning` 只给**需关注**（partial_success/interrupted），绛红 `destructive` 只给**失败/异常**。映射单点见 §1.3。
4. **cancelled / closed / draft / 停用 → neutral 灰 badge**。用户主动撤销 ≠ 系统异常，不再用红。
5. **dark mode 已启用（#872 PR2）**：三档偏好（浅色/深色/跟随系统，默认 system，D2 已批）；next-themes class 通道 + 内联脚本首屏不闪白；双端同源 token，无端侧独有配色。实测对比度与闭合证据见 §17；回退＝revert PR2（整体回浅色，PR1/#874 清理留存）。**首绘无内联取色圆点**：2026-11-13 实测（domcontentloaded 时刻取样 + 原始 SSR HTML，chromium 与 webkit 双端）确认 SSR 首绘 HTML 不含内联 style 取色的圆点，且此时 `.dark` 已就位；圆点在客户端取数完成后首次出现时即为暗色板。故不存在『暗色首绘浅色圆点』瞬态，此前登记的限制系误判，已撤销。证据归档：群共享 `shared/pr876-evidence/`（probe v1/v2 双端 JSON + run2/run3 日志）。

### 1.2 语义色 token 色值表（配色方案 v1）

> 对比度为对 light 页面底（白卡）的近似值。主色全部落在 5.1–6.0:1 区间（14px 正文数字直用合规）；foreground 对各自 soft 底全部 ≥6.5:1。Dark 列已为 #872 PR1 实测定稿值（对暗卡/暗底的实测数字见 §17）。表中 hex 是 HSL 声明的近似标注（±2/255 舍入），渲染色以代码声明为准（如 destructive `348 67% 46%` 渲染 `#C42746`，白字实测 5.64:1）。

#### 涨跌（金融方向色，专属数值场景）

| Token | 用途 | 色名 | Light | Dark 终值（#872） |
|---|---|---|---|---|
| `--color-gain` | 上涨数值、正向收益 | 朱红 | `#CF3526` hsl(5,69%,48%) ≈5.1:1 | `#F0735F` hsl(8,83%,66%) |
| `--color-gain-soft` | 涨相关浅底（高亮行、盈亏卡片底） | 浅朱 | `#FBEBE8` hsl(9,70%,95%) | `#3B2420` |
| `--color-gain-foreground` | gain-soft 上的文字 | 深朱 | `#A02718` hsl(7,74%,36%) | `#F5A89B` |
| `--color-loss` | 下跌数值、负向收益 | 松绿 | `#177245` hsl(150,66%,27%) ≈6.0:1 | `#3FB97F` hsl(151,49%,49%) |
| `--color-loss-soft` | 跌相关浅底 | 浅松 | `#E6F2EB` hsl(125,32%,93%) | `#1E3A2E` |
| `--color-loss-foreground` | loss-soft 上的文字 | 深松 | `#0F5C36` hsl(150,72%,21%) | `#8FD9B4` |

#### 状态色（业务状态语义，禁用于涨跌数值）

| Token | 用途 | 色名 | Light | Dark 终值（#872） |
|---|---|---|---|---|
| `--color-success` | 品牌主色 primary / ring、成功提示/选中态；**状态徽章映射见 §1.3**（#944 D3 后完成态徽章用 neutral，软靛蓝留给正常等待） | 靛蓝 | `#2B5CD7` hsl(223,68%,51%) ≈5.8:1 | `#7FA3F0` |
| `--color-success-soft` | 正常等待 badge（#944 D3）/成功提示底 | 浅靛 | `#E8EEFA` hsl(220,64%,95%) | `#22304F` |
| `--color-success-foreground` | success-soft 上文字 | 深靛 | `#1D47B0` hsl(223,72%,40%) | `#A8C2F5` |
| `--color-warning` | 需关注/待定（badge 映射见 §1.3） | 赭珀 | `#96620A` hsl(38,88%,31%) ≈5.2:1 | `#E5AC4A` |
| `--color-warning-soft` | 需关注 badge（#944 D3）/提示底 | 浅珀 | `#FBF3E2` hsl(41,76%,94%) | `#3D3020` |
| `--color-warning-foreground` | warning-soft 上文字 | 深珀 | `#7C4A03` hsl(35,95%,25%) | `#EBC88A` |
| `--color-destructive` | 异常/失败文字、状态圆点；solid 危险载体底见 `--color-destructive-solid` 行 | 绛红 | `#C22745` hsl(348,67%,46%) ≈5.7:1 | `#EE6B80` |
| `--color-destructive-soft` | 失败 badge 底、错误提示底 | 浅绛 | `#FAE8EC` hsl(347,64%,95%) | `#42242C` |
| `--color-destructive-foreground` | destructive-soft 上文字（solid 危险载体上的文字用纯白 `text-white`，载体见下行） | 深绛 | `#8F1A33` hsl(347,69%,33%) | `#F2A3B1` |
| `--color-destructive-solid` | solid 危险载体底（危险按钮/未读气泡，配纯白文字）；#872 D3 新增，light 与 `--destructive` 同声明、渲染逐字节不变 | 绛红（深） | = `--destructive` `348 67% 46%`（渲染 `#C42746`，白字 5.64:1） | `#C52B4A` hsl(347.92,64.17%,47.06%)（白字 5.50:1，vs 暗卡 3.03:1） |

**选色逻辑（为什么不是正红/草绿/正黄）**：涨红取朱红（5° 偏橙）——正红刺眼且与错误红同色相，朱红是账房/印章的传统「喜色」，69% 饱和度压住攻击性；跌绿取松绿（150°、27% 低明度）——鲜绿廉价感重且与「成功绿」国际惯例撞车；warning 压成赭珀（31% 明度）——正黄在白底上对比度无法达标（amber-500 仅 ~2:1），深琥珀棕是可读性达标的最浅解；绛红偏品红 17°——与朱红并置冷暖可辨，且品红向在国际上更常用于 error，双重线索。

**明度三轨**：① 信号主色统一 L 27–51% / 对比度 5–6:1（红绿并排视觉重量拉平）；② soft 底统一 L 93–95%（只给色相暗示，不抢内容）；③ soft 上 foreground 统一 L 21–40%，对底 ≥6.5:1。Dark 终值即三轨反转（主色 L 49–66%、soft 深色底、foreground 提浅），#872 PR1 已按轨落地并实测（§17）；solid 危险载体因白字对比度另走 destructive-solid 深绛轨（D3）。

### 1.3 状态 → Badge variant 映射

`badge.tsx` variant 全集：`default` / `secondary` / `success` / `warning` / `destructive`（soft 形态）/ `neutral` / `outline`。业务状态统一经 `getStatusBadgeVariant()`（`lib/utils.ts`）映射，禁止各页面自行发明状态色：

| 业务状态 | variant | 示例场景 |
|---|---|---|
| confirmed / active / success / passed / 启用 / cancelled / closed / draft / 停用 | `neutral` | 已确认交易、活跃组合、成功任务、已关闭/草稿/撤销/禁用（正常完成与终态，最高频「一切正常」不抢色） |
| pending / running / 在途 | `success`（软靛蓝低强调） | 待确认申赎/trade、运行中任务、跨天转移在途（正常等待） |
| partial_success / interrupted | `warning`（赭珀） | 部分成功、被启动期回收中断的作业（需关注） |
| failed / error | `destructive` | 失败任务、数据缺失告警、校验未通过 |
| （操作型危险按钮，非 badge） | solid `bg-destructive-solid text-white` | 删除、关闭组合等确认按钮 |

**#944 D3·S6 降噪口径**：正常完成→中性、正常等待→低强调靛蓝、需关注→琥珀、失败→异常红。现金转移组 `TRANSFER_GROUP_STATUS_META` 同口径（在途→`success`、已完成→`neutral`、已取消→`neutral`、状态异常→`destructive`）。首页待确认议程卡是「需关注议程」载体，显式 `bg-warning-soft` 保持琥珀，不走状态徽章映射。本次仅改映射、**无新 token**，§1.2 色值与 `theme.spec.ts` 金值不变。

**无状态语义的标识**（买卖方向、管理员角色、资产名目 chip）不许占用状态色：方向/角色用「neutral badge + 彩色小圆点」或 `default` variant，圆点色取自 `lib/colors.ts`（见 §2）。

### 1.4 中性色体系（蓝灰底，冷静/可信）

| Token | 用途 | Light | Dark 终值（#872） |
|---|---|---|---|
| `--background` | 页面底 | `#F7F8FA` hsl(220,23%,97%) | `#0F1420` |
| `--card` / `--popover` | 卡片/浮层底 | `#FFFFFF`（白卡浮于灰底，明度差第一层） | `#171E2C` |
| `--muted` | 次级底、neutral badge 底、hover 底 | `#EFF1F6` hsl(223,28%,95%) | `#232B3D` |
| `--border` | 边框、分隔线（装饰性，R1 口径不设 3:1 硬线） | `#E3E7EF` hsl(220,28%,91.5%) | `#2B3448` |
| `--input` | 输入框边界（识别组件所必需，≥3:1） | 同 border | `#606E8C` hsl(220.91,18.64%,46.27%)，vs 暗卡 3.26:1（#872 起与 border 解耦） |
| `--foreground` | 一级文字 | `#1A2333` hsl(218,32%,15%) ≈15.9:1 | `#E9EDF5` |
| `--color-foreground-secondary` | 二级文字（表头、标签） | `#5A6577` hsl(217,14%,41%) ≈5.9:1 | `#A6B0C6` |
| `--muted-foreground` | 三级文字（12px 辅助、占位符） | `#64708A` hsl(221,16%,47%) ≈5.0:1（为 12px 辅助文字守 4.5 底线，**禁止再浅**） | `#8A94AB`（最劣 muted 底 4.65:1，**禁止再深**） |
| `--primary` / `--ring` | 主按钮、链接、选中态、focus 环 | = success `#2B5CD7` | `#7FA3F0` |

> 目标值已于 2026-08-29 一次性切换落地（此前为「shadcn 默认 slate 现状值 → 目标值」双轨渐进）；`--secondary` / `--accent` 底与 muted 同源同值。层级策略：页面灰底 → 白卡浮起 → 边框轻勾勒，三级明度差小、扁平金融风；文字三级 15/41/47% 明度。Dark 终值于 #872 PR1 落地（HSL 2 位小数精确往返登记 hex，实测数字见 §17）；card/popover/secondary/accent 的 `-foreground` 变体与 foreground 同值（沿 light 模式）；`--primary-foreground` dark = `#0F172A`（深字配亮靛蓝底，7.14:1）。

### 1.5 护栏（ESLint）

`eslint.config.mjs` 内置 `no-restricted-syntax`（error），两道拦截（均含字符串字面量与模板字符串）：

1. **调色板类名**：禁止 `(text|bg|border)-(red|green|yellow|blue|amber|emerald|orange|purple|indigo|pink|teal|cyan)-<数字>`。新增颜色需求一律先加语义 token。
2. **任意值类名**（2026-08-29 起）：禁止 `text-[…]`（§5 四档之外）、`p/px/py/pt/pb/pl/pr-[…]`、`gap/gap-x/gap-y-[…]`、`rounded-[…]`（§7 派生档之外）。m 系 / space 系 / w·h 系任意值**暂不拦截**（多为视口比例或一次性尺寸，如 `h-[60vh]`、`sm:max-w-[500px]`，是否纳入待后续评估）。**能力边界（#935 校正）**：本拦截只覆盖任意值类，**不能完整判定命名字号的语义角色**——`text-lg`/`text-2xl` 用于 §5 登记的数值/品牌角色合法、用于普通标题违规，工具类本身不被 ESLint 拦；角色归级靠 §19 自检与评审，不新增全站标题扫描器或另一套规则源。
3. **中性色 ratchet**（2026-11-13，#872 PR2，D6 已批）：禁止 `(text|bg|border)-(slate|gray|zinc|neutral|stone)-<数字>`（字符串与模板字符串同拦）。中性色硬编码已于 PR1/#874 清零，本道防回流；`white`/`black` 为刻意例外（solid 危险载体白字、Dialog/AlertDialog scrim）不在本规则面；渐变端点色 `from-`/`to-` 不在 D6 已批范围、未拦截（扩面须维护者另行批准）。

另有 `no-restricted-imports`（error，2026-09-01 #349 起）：业务代码禁止从 `@/lib/utils` import 裸 `formatShares`——份额上屏一律 `formatSharesUnit`（§3/§12），`formatShares` 仅供其内部实现。成因：#249 全站清零手工扫描仍漏掉三个确认弹窗，证明纯约定不可持续，升级为编译期门禁。

**豁免清单**（overrides 登记，临时项只减不增、收敛后移出；豁免文件仍受调色板拦截）：

| 范围 | 类型 | 理由 |
|---|---|---|
| `src/components/ui/**` | 永久 | shadcn 基件 vendor 源码（`min-w-[8rem]` 等为官方实现），保持与上游同步、降低升级摩擦；基件内部不视为业务违规 |
| `src/components/layout/NotificationBell.tsx` | 临时（ratchet） | 存量 `text-[10px]` 1 处，改动该组件时顺手收敛后移出 |
| `src/lib/utils.test.ts` | 永久 | 单测需直测 `formatShares` 基础函数（`formatSharesUnit` 的内部实现），仅豁免 `no-restricted-imports` 门禁 |
| `src/lib/logger.ts`、`src/lib/logger.test.ts` | 永久 | 前端日志基建（#407）：前者是全站 `console.*` 的唯一封装层，不豁免则该护栏无法实现；后者需对 console 方法做 spy 才能断言分级与生产剔除（同 `utils.test.ts` 形态），仅豁免 `no-console` |

确需新增豁免时在代码处加 `eslint-disable-next-line` 并在本节逐条登记（位置 + 理由）。

---

## 2. 图表色板（`lib/colors.ts` 唯一来源）

基于 **Okabe-Ito 色盲安全色板**改造，主动避开 gain 朱红（5°)、loss 松绿（150°)、destructive 绛红（348°）三个已占用色相，饼图切片不会被误读为涨/跌/异常。**组件内禁止出现 hex 字面量**；recharts / 内联 style 等必须用 hex 的场景一律从 `lib/colors.ts` 取：

| 导出 | 值 | 说明 |
|---|---|---|
| `CHART_COLORS[0]` C1 靛蓝 | `#2F5FD0` | 第一序列色；与品牌/success 同色相是刻意的（品牌一致性） |
| `CHART_COLORS[1]` C2 琥珀金 | `#E8A33D` | |
| `CHART_COLORS[2]` C3 天蓝 | `#56B4E9` | Okabe-Ito 原色 |
| `CHART_COLORS[3]` C4 堇紫 | `#7E69D8` | |
| `CHART_COLORS[4]` C5 玫紫 | `#CC79A7` | Okabe-Ito 原色 |
| `CHART_COLORS[5]` C6 赭橙 | `#C9762E` | 与 C2 靠明度（57% vs 48%）+ 饱和度区分 |
| `CHART_COLORS[6]` C7 灰蓝 | `#8A97AC` | 低饱和，**专供「其他」合并项**（`CHART_OTHER`） |
| `CHART_COLORS[7]` C8 深灰蓝 | `#4A5578` | 备用第 8 色 / 次数据线 |
| `NAV_LINE` | = C1 | 净值曲线主线恒为靛蓝，**不用红绿**；涨跌靠坐标轴数值与 tooltip 的 `text-gain/loss` 表达 |
| `assetClassColor(sortOrder)` | 股票=C1 / 债券=C4 / 商品=C2 / 现金=C7 | 资产大类色（#128 起字典驱动）：`ASSET_CLASS_PALETTE` 按 asset_class 字典 sort_order 取色；饼图（`buildAllocation`）与持仓分区共用（`lib/allocation.ts` 消费）；原「黄金」序位由「商品」承继 |
| `IN_TRANSIT_COLOR` | 在途=C3 天蓝 | 在途资金伪大类（现金的轻量态），固定插现金大类后 |
| `OTHER_COLOR` | 资产「其他」=C8 深灰蓝 | 资产分布「其他」伪大类（派生缺失/字典未收录的兜底），固定垫底 |
| `TRADE_DIRECTION_COLORS` | buy=C1 / sell=C6 | 仅用于买/卖方向小圆点，不表达涨跌 |

**色盲友好性（如实说明）**：Deuteranopia/Protanopia 下 C1↔C4、C2↔C6 会趋近，靠 ≥7% 明度差与图例位置兜底；**超过 6 类必须合并为「其他」（C7），这是规范条目而非建议**。Tritanopia（极罕见）下全体可区分。

**两个「其他」色，不可混用**（2026-08-29 评审补登）：通用多序列合并项用 `CHART_OTHER`（C7 灰蓝）；资产分布的「其他」伪大类用 `OTHER_COLOR`（C8 深灰蓝，#128）。边界原因：**现金大类色 = C7**——资产饼图若合并项也用 C7，会与现金切片同色无法区分。故资产维度一律走 `assetClassColor` / `OTHER_COLOR`，`CHART_OTHER` 只用于无「现金」语义的普通多序列。

**已知耦合（#128 登记）**：`assetClassColor` 以 `ASSET_CLASS_PALETTE` 序位绑定 asset_class 字典 `sort_order`——**调整字典排序即换色**。改 sort_order 前须评估饼图/分区配色连续性；新增第 5 大类时序位超出现有 4 色将兜底 C8（与「其他」同色），届时须先扩 `ASSET_CLASS_PALETTE`。

**暗色色板与 theme 访问器（#872 PR1 就绪，PR2 消费）**：`DARK_CHART_COLORS` 仅提亮 C1/C4/C8，其余沿用 light hex（最小化双套差异）；对暗卡 `#171E2C` 实测全部 ≥3:1。访问器 `chartPalette` / `navLineColor` / `chartOtherColor` / `inTransitColor` / `otherColor` / `assetClassColor(sortOrder, theme?)` / `tradeDirectionColors` 默认 `"light"`，返回值与既有常量全等；`buildAllocation` / `cashFlowDotColor` 带可选 theme 参数透传（vitest 双态覆盖）。组件在 PR2 经 `useTheme().resolvedTheme` 传入；涨跌色永不走色板（§1.1）。

| 序位 | Light | Dark | 暗卡对比度 |
|---|---|---|---|
| C1 靛蓝 | `#2F5FD0` | **`#5C82E0`** | 4.55（light 值 2.92 不达标，提亮；NAV_LINE dark 同值） |
| C2 琥珀金 | `#E8A33D` | 沿用 | 7.74 |
| C3 天蓝 | `#56B4E9` | 沿用 | 7.23 |
| C4 堇紫 | `#7E69D8` | **`#9C8AE8`** | 5.73（light 值 3.89 余量 <1，提亮留余量） |
| C5 玫紫 | `#CC79A7` | 沿用 | 5.45 |
| C6 赭橙 | `#C9762E` | 沿用 | 4.86（与 C2 明度差 9.0pp 保持） |
| C7 灰蓝 | `#8A97AC` | 沿用 | 5.64 |
| C8 深灰蓝 | `#4A5578` | **`#7A87AD`** | 4.69（light 值 2.27 不达标，提亮） |

色相核对：dark H = 222.7/35.8/201.6/251.5/326.7/27.9/217.1/224.7，避开 gain 8°/loss 151°/destructive 350° 占用色相；NAV_LINE dark 仍非红绿；「超 6 类合并其他」「两个其他色不可混用」规则不变。

**recharts 默认色治理结论（#872 A③，已于 PR2 落地）**：库默认轴色 `#666` 在暗卡仅 2.91:1（12px tick 不达标）、Tooltip 默认白底 `#fff` + `#ccc` 边框属 C1 禁止的浅底残留——两者是库默认值，仓内 hex grep 抓不到。已于 #872 PR2 对 NavCurve/AssetAllocationPie 显式主题化：轴 tick fill 走 foreground-secondary token、轴线走 border token、Tooltip `contentStyle`/`itemStyle`/`labelStyle` 走 popover/border token；`CartesianGrid` 用 `stroke-muted` 自动随主题。

---

## 3. 数字格式

**金融数字必须走 `lib/utils.ts` 格式化函数，禁止组件内 `toFixed` / 手写千分位**（占比例外见 §4）：

| 数据类型 | 函数 | 规则 |
|---|---|---|
| 金额 | `formatCurrency` / `formatCompactCurrency`（概览大字） | 千分位 + 2 位小数 + `¥` |
| 份额 | `formatShares` | 固定 2 位小数，不带货币符号（勿用 formatCurrency 代替） |
| 份额带单位 | `formatSharesUnit` | `formatShares` + 「份」后缀（`8,933.89 份`，负数 `-1,000.00 份` 符号在数字内、单位在外）；份额上屏一律用它，禁止手写 `` `${formatShares(x)} 份` `` 模板串（#249） |
| 净值/价格 | `formatNav` | 固定 4 位小数，不带 `¥` |
| 精确对账金额 | `formatAmount4` | 4 位小数，仅用于与后端/CLI 对账场景 |
| 百分比/收益率 | `formatPercent` / `formatReturnRate` | 默认 2 位小数、自带 `+/-` 符号 |
| 表格数值单元格 | `number-cell` utility（`text-right font-mono tabular-nums whitespace-nowrap`，现状见 globals.css） | 右对齐 + 等宽数字 + 不折行（#394）。**漂移台账**：`lib/utils.ts` 的 `getNumberCellClass()` 返回**不含** nowrap 的三件套（固定串被 `utils.test.ts` 钉住），经 helper 拼类的表格须自行拼 `whitespace-nowrap`（投资人列表表现例，#693）；helper 全仓收敛会连带改变另一生产消费点 `SnapshotsContent` 的换行行为，留单独双端目检裁决，此前维持现状。**卡片窄列的另一类例外（2026-10-20，#724 登记）**：持仓卡的卡内明细列（`HoldingProductCard.tsx::HoldingProductCard` 的 row3 两列收益、`HoldingPlatformCard.tsx::HoldingPlatformCard` 的持有收益段）刻意用 `tabular-nums` 而**不是** `number-cell`——`font-mono` 字形更宽，窄卡（移动 1 列）下会把收益值截成 `+537.1…`，而金融数值不可 ellipsis（#636 目检实证；2026-10-20 起产品卡 row3 两列已改 `whitespace-nowrap` 不截断、平台卡持有收益段仍为既存 `truncate`，见 §12）；这些列不是表格数值列，§8「禁止内联 `font-mono tabular-nums`」不据此扩面到卡内明细 |
| 无效值 | 各函数 `fallback` | 统一回显 `--`，不显示 `NaN` / `null` |

## 4. 占比精度分层

- **行级占比 1 位小数**：一律经 `largestRemainderPercents`（最大余数法，issue #99），全部行加总恒为 100.0%；禁止各处自行 `toFixed(1)`（会产生 ±0.1%×n 漂移）。**渲染口径**：返回值为百分比数值（如 `62.4`），展示统一 `percent.toFixed(1)%`（保底一位小数，`62.0%` 不缩为 `62%`）；**不要**走 `formatPercent`——其输入为小数形式、默认 2 位且带 `+` 号，口径不符。
- **分区头/聚合占比取整**：分区头、chip 合计由行级占比**加总后取整**，不再独立计算，保证同分区口径一致（issue #114）；名目 chip 合计恒显示，即使只有一行。
- 饼图图例直接展示行级加总的 1 位小数值（`buildAllocation` 输出），与分区头严格自洽。

## 5. 字号四级

| 级别 | 规格 | Tailwind | 用途 |
|---|---|---|---|
| 页面标题 | 20px / 600 / 28px | `text-xl font-semibold leading-7` | PageHeader 与各页 h1（#935 起，原 24px 档）；展开/折叠一致，不随滚动改字号 |
| 分区标题 | 16px / 600 / 24px | `text-base font-semibold leading-6` | CardTitle 默认、Dialog/AlertDialog/Sheet 标题、产品全名 h2、普通卡片/内容分区（#935 起，原 18px 档） |
| 正文 | 14px / 400 / 22px | `text-sm leading-5.5`（新改位置） | 返回/面包屑导航、产品身份说明（代码/市场）、一般正文；既有表格/图表/控件/紧凑正文的布局行高保持不变，不做全站重刷 |
| 辅助 | 12px / 400 / 18px | `text-xs leading-4.5`（新改位置） | 非关键提示和辅助信息，不承载唯一产品识别信息；既有辅助行高不变 |

**数值/品牌专属角色（#935 登记，不在文字四档内、不按普通分区机械升降档）**：`amount-large` 24px 重点金额（globals.css utility）；移动金额 18px（`text-lg font-bold …`，统计卡移动主数值/持仓卡市值/快照状态数值等）；桌面重点金额 24px（`text-2xl`，统计卡主数值/概览持仓市值/持仓合计/日历统计）；**首页总资产 hero** 使用 `amount-hero`（移动 36px / `text-4xl`，桌面消费端叠加 `lg:text-5xl` = 48px，`font-semibold tabular-nums`），其他主数值仍保持 24/18px；存量 `text-xl` 数值中间档（`PerformanceMetrics`/`LedgerContent`/`LedgerPerformance` 主数值）；登录品牌标题（`text-2xl font-bold leading-none`）；图表标注 12px；14px 紧凑提示卡标题（`text-sm leading-none`，首页待确认提醒卡等）。数值字体/格式/对齐与既有串长/容器宽保护（下文统计卡各条）全部不变。

配套规则：表格与右置明细的数值一律叠加 `number-cell`（统计卡主数值不套 `number-cell`，对齐见下条）；**文字四档为 20/16/14/12**——`text-3xl` 与 `text-[Npx]` 任意值属违规（任意值由 ESLint 拦截，见 §1.5；存量 1 处豁免登记在 §1.5，改动页面时顺手收敛，不强制一次性清零）；`text-lg`/`text-2xl` 仅允许出现在上段登记的数值/品牌专属角色，普通标题不得继续使用。**ESLint 只拦任意值类，不能完整判定命名字号的语义角色**（§1.5），角色归级靠 §19 自检与评审核对。22/18px 行高用 Tailwind spacing 行高工具（`leading-5.5`/`leading-4.5`，v4 动态 spacing 档），不用 `leading-[22px]` 任意值。

**首页汇总（#944 Phase 3.2）**：`DashboardSummary` 只呈现总资产、累计收益合计金额、合计年化收益率（XIRR）三项核心指标，不再使用首页四卡栅格。总资产消费 `amount-hero lg:text-5xl`（移动 36px / 桌面 48px），另外两项沿用桌面 24px / 移动 18px；累计收益用 `formatSignedCurrency` 并随数值涨跌着色，XIRR 不可靠时用 `--` 与原因说明。平均收益、活跃/全部组合数、投资人数和首页资产构成均不属于首页指标。

**统计卡（概览四卡）对齐**（#725 登记）：统计卡内**标签、主数值、注记同侧左对齐**，不因主数值是带符号/小数金额而右置——组合详情四卡（`PortfolioStatsCards`）与现金流水页概览四卡均为此**左对齐口径**（本条只约束对齐，不统一字号与字体）。现金流水页概览四卡的实现形态：主数值保留数字形态（`font-mono tabular-nums whitespace-nowrap`），拼类走 `getNumberCellClass()` 再以 `text-left` 覆盖（`cn`/twMerge 保证覆盖生效，该组合有断言钉住，见 `src/lib/utils.test.ts`），不直接挂自带 `text-right` 的 `number-cell` utility；字号桌面 `text-2xl`、移动 `text-lg`——移动 2 列栅格下 24px 的带符号大额会溢出压到邻卡、吞掉负号，故降一档，两档均为本节登记的金额数值档（#935 起属数值专属角色）、移动档与 `PortfolioStatsCards` 同档，不属 §6 禁止的「端侧独有字号」。**已知边界与已落地的栅格降档（#727 第二轮 L2 实测；#730 收敛，2026-10-20）**：隔离栈探针 390px 双列栅格、卡内容宽 146px 下，`text-lg` 带符号 **7 位**金额需 151px、溢出 5px 落在 16px 列间距内不与邻卡文字相碰；**8 位**需 162px、吃满列间距与邻卡数值相接（不叠印）；`text-xl` / `text-2xl` 分别叠印约 22px / 70px（数值互压不可读）；桌面 1280 四列 `text-2xl` 卡内容宽 215.5px（#762 实测，旧登记 216 为其取整，推导见本条末），文本墨迹与邻卡的实测间隙随位数递减：8 位带符号（15 字符）15.45px、9 位（16 字符）仅 1.06px、10 位（18 字符）−27.75px 起叠印、11 位（19 字符）−42.16px 且末卡墨迹右缘 1281.16px 越过 1280 视口（#745，同机 Linux + Chromium 的 mono 回退实测；像素随宿主字体漂移，失效机制与字体无关）。**字符数不等于位数**：整数部分每跨一个千分位就多一个逗号，8→9 位是 +1 字符（15→16），9→10 位是 +2 字符（16→18，多出 `1,234,567,890` 的那个逗号），10→11 位又回到 +1（18→19）；按位数推算阈值会差掉这个逗号（≈14.4px），判据一律数渲染串长度（串字面量见 `e2e/cash-flows.spec.ts` 的 `rendered` fixture）。当时登记的**失效条件**是「降档只缓解不根治，彻底解决须改栅格或缩略显示、未排期」——已按**改栅格**落地：移动端任一卡**格式化后字符串长度 ≥ 15**（如 `-¥12,345,678.90`）时，概览四卡栅格由 `grid-cols-2` 降为 `grid-cols-1`（单列内容宽约 308px，8 位乃至 9 位均充裕），**字号不再降档**、保持 `text-lg`（再降 `text-sm` 会让主数值与正文同档、失去层级）。判据取展示串长度而非数值量级——同为 1e7 量级，正金额 `¥12,345,678.90` 是 14 字符（实测 151px、溢出 146px 内容宽 5px，落在 16px 列间距内不与邻卡文字相碰），带负号才达 15 字符（162px、吃满列间距与邻卡相接）；实现见 `CashFlowsContent.tsx::wideValue`（移动端 ≥15 字符降 `grid-cols-1`）与 `wideValueDesktop`（桌面 ≥16 字符直接落两列、不按容器宽加回四列，卡内容宽约 448px，11 位亦充裕）；两者同消费 `CashFlowsContent.tsx::statCards` 已格式化的串，不另起第二条数值→文本路径。**两端判据都不看视口宽**——只看串长，以及（自 #772 起）桌面列数看**栅格所在容器**的宽度，故上述登记值全部以实测视口（桌面 1280 / 移动 390）为准。**窄窗区间历史实测（#762，2026-10-23，同机 Linux + Chromium；以下容器/轨道数值均属 #944 前含侧栏形态，仅用于解释阈值来源；当前桌面 TopNav 不占横向宽度，见 §20.2；本段 1024/1152 两档是 #772 修复前形态，修复后见本条末；隔离栈把四卡都注成 `-¥12,345,678.90`／15 字符，量文本节点 `Range.getBoundingClientRect()` 并逐层量 `getBoundingClientRect()`）**：卡内容宽与邻卡墨迹间隙在 1024 / 1152 两档同为 **203.45px / +3.41px**，1250 档为 **208px / +7.95px**，1280 档为 **215.5px / +15.45px**（与本节既有登记值逐位相符，方法自校验）；间隙恒等于「卡内容宽 − 200.05px」（该串墨迹宽 216.05px 减去 `gap-4` 的 16px；像素随宿主字体漂移，关系式不随字体变）。**结论：`lg` 全区间（1024–1280）无墨迹叠印**——#745 遗留的几何外推「约 1218px 起转负」在真实 DOM 上不成立，它假设卡内容宽随视口线性收窄，而实测卡内容宽有**下限 203.45px**：`main`（`flex-1 p-4 lg:p-8`）作为 flex 子项带自动最小尺寸，1024 / 1152 两档其宽度恒为 975.84px、不再随视口变化，即已抵到自身最小内容宽（钉住该值的具体元素由 #772 定位为**明细表**：`main` → `div.space-y-6` → 明细 `Card` → `CardContent p-6` → `div.relative` → 表外层 `div.w-full.overflow-auto` → `table` 逐层等宽（实测 975.84 → 911.84 → 909.84 → 861.84 = 表最小内容宽，七列表头之和），块级链上的 `overflow-auto` **不**削减向上传递的 min-content）。视口不够时先挤 sidebar——`aside.hidden lg:block w-64` 未加 `flex-shrink-0`，1152 档被压到 176.16px、1024 档压到自身最小内容宽 99px——仍不够才整页横向溢出（1024 档 `document.scrollWidth` 1075 > 1024，末卡墨迹右缘 1030.42px 越出视口约 6px）。**故窄窗的真实残余形态是「sidebar 被压窄 + 更窄时整页横向滚动」，不是「邻卡文字相碰」**（该残余已由 #772 按下文三处同批改掉）；这是**两件事、两个区间**，不要合成一个：**sidebar 被压窄**出现在 1024 ≤ 视口 < 约 1232px（= 975.84 + 256，sidebar 开始被压的临界点）；**整页横向溢出**只出现在其中更窄的一段——由「`main` 最小宽 975.84 + sidebar 最小宽 99 ≈ 1074.84」推得约 视口 < 1075px（实测 1024 档溢出、1152 档无溢出，两档之间的精确阈值未测），落在 `lg` 区间内即约 1024 ≤ 视口 < 1075px。<1024 时 `lg:` 失效、栅格本就两列，不在本条范围。**卡内容宽推导（订正 #757 的 178px 口径）**：1280 − sidebar 256 = main 1024；− `lg:p-8` 两侧 64 = 960（Card 外框，`rounded-lg border`，含 1px 边框）；− 边框 2 − `CardContent` 的 `p-6` 两侧 48 = 910（栅格容器实测宽）；− `gap-4` × 3 = 48 → 862；÷ 4 = **215.5px**，与逐层实测值一一相符。实测 DOM 链只有**一层** Card + 一层 CardContent，四张统计卡是该 Card 内的 grid 子项（`div.flex.flex-col.gap-1[data-testid="stat-*"]`，自身无边框、无 `p-6`）；178px 的逐项推导按「每张统计卡各带一层边框 + 内边距」扣减，与实际链不符，作废。**不引入视口判据**（#745）在「降串长阈值」这条路上仍然成立——把桌面阈值降到 ≥15 会拖累常见 8 位值进两列，已论证拒绝、#772 也没走这条路；但它对**列数判据**的否定被 #772 修订为「按容器宽、不按视口宽」。窄窗横向溢出与 sidebar 压窄属页面级布局问题、与统计卡字号无关：#770 判定「不能只靠给 `main` 加 `min-w-0`」是对的（单独加它，卡内容宽跌破 203.45px 下限、间隙按上式转负成真叠印），#772 的处置是**三处同批**——① `MainLayout.tsx::main` 加 `min-w-0`（解除 flex 子项的内容自动最小宽，明细表改在自身 `overflow-auto` 容器内横滚）；② 历史 `Sidebar.tsx` 的 `aside` 加 `shrink-0`（当时用于避免导航宽度让位）；③ 桌面列数改按**容器查询**取宽：概览卡的 `CardContent` 标 `@container`，栅格由 `lg:grid-cols-4` 改为 `@min-[848px]:grid-cols-4`，848 = 4×(216.05 − 16) + 3×16，即四列轨道各装得下 15 字符墨迹（减去 `gap-4` 可吸收的 16px）所需的最小容器宽。用视口断点判不了这件事：`lg:` 把「视口 ≥1024」当成「内容有 1024」，而 `main` 实得 视口 − 256 − 64。修复后实测（同机 Linux + Chromium，15 字符夹具，#772 一次性隔离栈探针）：1024 档 `main` 768 / 容器 654 → **两列 319px / 间隙 +118.95px**；1152 档 896 / 782 → 两列 383px / +182.95px；1250 档 994 / 880 → 四列 208px / +7.95px；1280 档 1024 / 910 → 四列 215.5px / +15.45px——后两档与上面的登记值逐位相符（容器 880/910 ≥ 848 才保持四列），前两档由四列转两列，`lg` 全区间无叠印的**结论不变、达成方式变了**（不再靠 `main` 的内容下限撑宽）。四档 `document.scrollWidth` 均等于视口宽，历史侧栏四档恒 256px。1024 档已由 `e2e/cash-flows.spec.ts` 的「概览页窄桌面档（#772/#944，1024 视口）」用例钉住 `scrollWidth ≤ innerWidth`、TopNav 静态且无侧栏、main 铺满布局视口、四列且同排墨迹间隙 ≥8px（`test.use` 单设 1024 视口、仅 chromium project）；1152/1250 两档仍无自动化覆盖，旧含侧栏取数靠一次性隔离栈探针且未入库，当前 TopNav 形态触及时按本条重测。`<16` 字符的行为与 #745 修复前逐字相同，本条残余非该修复引入。**两端阈值刻意不共用一个长度值**：列宽（146px vs 215.5px）与字号（`text-lg` vs `text-2xl`）都不同，同长度在两端的安全余量差一个量级——8 位带符号在桌面尚余 15px、在移动端已相接。§6 允许「移动端网格列数更少」，此降档属该类布局差异、不属端侧独有字号。触及以上形态时按本条复核，不要只凭「已在四档内」判定合规。`PortfolioStatsCards` / `DashboardStatsCards` 自 #904 P0b 起桌面统一走 `cn(getNumberCellClass(), "text-left whitespace-nowrap font-semibold text-2xl")`（与现金流水页同形），容器阈值随 mono 墨迹 216.05px 由 804 重标定为 848（§5 公式）；移动维持登记档（`text-lg font-bold tabular-nums`），mono 化连带 wideValue 串长降列留后续批；组合总资产卡涨跌 soft chip 双端同增（`getReturnBgClass` 底 + `getReturnSoftTextClass` 字——soft 轨 foreground，§1.2/§17 ≥6.5:1；P0b 初版误配主色 `text-gain`，light 正收益 4.38:1 低于 AA，经 L2 打回修正；§6 信息列双端一致）。`number-cell` 的右对齐**只用于表格数值列（§8）与右置明细数值**（如 §12 对账明细紧凑列），不用于统计卡主数值；该 utility 挂在 inline / flex 子项这类**内容宽容器**上时 `text-right` 不产生可见右对齐（既有例子：`HoldingProductCard.tsx` 标题行市值 span、`CashFlowsContent.tsx` 移动明细卡金额 span，均为 flex 子项），不属本条约束对象；块级列表卡的主数值（如 `HoldingPlatformCard.tsx` 持仓市值、`PlatformDetailContent.tsx` 现金余额卡）`text-right` 实际可见，但它们是平台/产品列表卡、不在本条「统计卡（概览四卡）」范围内，维持现状、触及时按本条重新评估。**既有例外已归零（#729 收敛，2026-10-20）**：产品详情 / 平台详情 / 平台-产品详情三处概览卡的「持仓市值」大字（`ProductDetailContent.tsx::overview` / `PlatformDetailContent.tsx::overview` / `PlatformProductDetailContent.tsx::overview`）原为模板串直拼的 `mt-1 text-2xl font-semibold number-cell`，`text-right` 随该 utility 一起继承而右置；现统一为本条登记的同形写法 `cn(getNumberCellClass(), "text-left whitespace-nowrap font-semibold text-2xl mt-1")`——字号 `text-2xl`、字重 `font-semibold`、间距 `mt-1` 与数字形态 `font-mono tabular-nums whitespace-nowrap` 均保持不变，只由 twMerge 把 helper 自带的 `text-right` 覆盖为 `text-left`。`globals.css` 的 `number-cell` utility 与 `getNumberCellClass()` 本身零改动，表格数值列（§8）与右置明细数值（§12）的右对齐不受影响。

**统计卡栅格按容器宽降档（#774 登记，2026-10-23，同机 Linux + Chromium；⚠️ 本段实测值与 804 阈值为 mono 化前历史记录；#944 起侧栏已删除，以下容器宽度只作历史阈值来源，当前形态见 §20.2）**：自 #904 P0b 起 Portfolio/Dashboard 改 mono 墨迹 216.05px、阈值重标定 848（见上段）；1152 档容器 832px 四列轨道 196px、间隙 ≈ −4.05px，故**该档转两列是 mono 化的预期代价，不是「白降」**（本段原「1152 保四列」结论随之失效）；`aside` 256/224 等侧栏实测数均属 #944 前历史值，侧栏已删除，不再作为当前宽度判据。#772 的容器查询降档当时只落在现金流水页概览四卡，与之同构的 `PortfolioStatsCards`（组合详情）与 `DashboardStatsCards`（首页）仍是 `md:grid-cols-2 lg:grid-cols-4`——`main` 加 `min-w-0` 后这两页 1024 档的栅格容器由 750px 收到 704px、四列轨道由 175.5px 压到 164px，注入 8 位带符号金额后**组合详情页同排邻卡墨迹间隙由 +0.5px 转 −11px**（即 #772 在这两页引入了叠印）；首页修复前后两形态逐位相同（`aside` 256 / `main` 768 / 容器 704 / 轨道 164px，间隙 −15px）——该页无明细表钉住 `main`、宽度不随 #772 变化，属既存缺陷，与组合详情同批按同口径修。处置：两处桌面分支各套一层 `@container` **父**元素（容器查询按最近祖先容器求值，挂在栅格自身不生效），栅格改 `md:grid-cols-2 @min-[804px]:grid-cols-4`（历史阈值，现 848）；移动分支（`grid-cols-2`、`text-lg`）不动。**804 与现金流水页的 848 是同一条公式**「容器 ≥ 4×(墨迹 − gap) + 3×gap」：同排墨迹间隙 = 轨道 + gap − 墨迹（每卡自带的 1px 边框与 `p-6` 在相邻两侧相消，不进该式），两值只差墨迹宽——15 字符 `¥-12,345,678.90` 在组合详情 205px（`font-bold tabular-nums`）、首页 196px（无 `tabular-nums`），现金流水页同串走 `font-mono` 为 216.05px。**判据只看容器宽、不看串长**（与 #745「不引入视口判据」同源）。修复后各档实测（每档两值依次为**合成最坏**＝四卡全注成同一 15 字符串、**真实注入**＝只把金额卡注成 8 位带符号）：1024 档容器 704 → 两列轨道 344px、间隙 +155/+169；1152 档 832 → 四列 196px、+7/+21；1250 档 930 → 四列 220.5px、+31.5/+45.5；1280 档 960 → 四列 228px、+39/+53——**8 位带符号（≤15 字符）在 `lg` 全区间无叠印**，且 1152/1250 两档保住四列（历史结论：mono 化前墨迹 205/196px 下阈值若沿用 848，容器 832px 的 1152 档会白降两列；mono 化后该结论失效，见段首注）。**已知边界（10 位、18 字符，未修）**：容器查询看不到串长，18 字符（墨迹 229–243px）在 1152 档四列仍叠印 −17（真实注入）/−31（合成最坏），1250 档 +7.5/−6.5、1280 档 +15/+1，1024 档两列则充裕（+117…+136）；移动 390 档两列轨道 157px，8 位带符号 +26.49、10 位 −1.61。本轮不扩口径：抬阈值到 956（= 4×(243−16)+48）能盖住 10 位，代价是 1152/1250 两档**所有**数值都退两列；改引现金流水页那条串长判据（`value.length >= 16` 直接两列）则要在这两个组件里再加一条与容器宽正交的判据。个人/家庭组合总资产达 1e9 元不在本系统现实量级，故只登记不修，触及时按本条重测。**自动化覆盖**：`e2e/regression.spec.ts` 的「统计卡栅格宽视口与移动端（#774）」与「统计卡栅格桌面 1024 档（#774/#944）」两组用例钉住 1280/1024 档四列单行、移动 390 档两列，三者都另断同排墨迹间隙 > 0（量法同 `cash-flows.spec.ts::statValueInks`，锚点是组件上的 `data-testid="dashboard-stat-*"` / `data-testid="portfolio-stat-*"`）；1024 档在 #944 前为两列，侧栏删除后随容器增至约 910px 恢复四列。现金流水页的四列分支同时在 `e2e/cash-flows.spec.ts` 的「#730 不过度降档」用例内补了 1280 档四列单行断言（此前只有「同排、第二卡在右」的弱断言，两列下同样为真）。两处四列断言都做过**变异验证**：把 `@min-[804px]:grid-cols-4` / `@min-[848px]:grid-cols-4` 改成笔误类名后分别变红。1152/1250 两档仍无自动化覆盖，取数靠一次性隔离栈探针、未入库。

**字体栈（现状登记，2026-11-27）**：`app/layout.tsx` 通过 `next/font/google` 的 `Inter({ subsets: ["latin"] })` 生成类并挂在 `<body>`；拉丁字符走 Inter，中文由该字体栈回退到系统字体。数值列仍由 `number-cell` 使用 `font-mono`（ui-monospace/SFMono 系），`tabular-nums` 依赖字体自带的等宽数字特性；跨平台中文与数字渲染一致性尚未实测。如需严格统一金融报表字形，再评估引入统一数字字体并在此更新决策。

## 6. 双端差异约定

- **同一份语义，两套壳**：移动端 `/m/` 薄壳页 + `MobileLayout`，业务内容走 `components/shared/` 共享组件（`variant: "desktop" | "mobile"` + `basePath` 适配）；颜色、数字格式、占比精度**双端完全一致，不允许端侧各自着色**。
- 布局差异只体现在四类：① 移动端网格列数更少（如统计卡 `grid-cols-2`）；② 表格优先改卡片列表；③ 操作按钮收进图标/抽屉；④ **纯装饰性层级线索可在移动端让位于横向空间**（#724 曾为持仓维度组保留桌面引导线；#944 Phase 7.1 桌面改通栏账表后，该引导线与缩进随卡壳一起退役，组头与行序仍表达归属）。**不发明移动端独有的色彩或字号**；**信息列的增删必须双端一致，不属布局差异**——同一产品在两端呈现的字段集合相同，需要收窄信息时双端同收窄（#724 移除持仓产品「持有份额」列即双端同移，份额由两个消费者各自的详情页概览卡承载——逐消费者的去向由 §12 持有），不为移动端单独裁剪信息结构。筛选栏 / 日期区间 / 分页三类控件的端侧形态差异分别见 §9 / §10 / §11，本节只定原则。
- **移动产品聚合行（#944 Phase 6.2）**：组合「按产品」视图的移动分支沿用同一产品字段、码标签、占比精度与累计/最新收益口径，但壳由圆角卡片改为 `divide-y` 分隔线行（桌面使用 §8 通栏账表）；这是 §6② 的列表布局差异，不裁信息列。现金副标双端统一为「可用现金」，平台分布从行副标移至产品详情，避免把跨平台聚合误读为单平台余额。行覆盖链接携带机器码 `data-product-code` 与 code-market 全键 `data-product-key`；点击后按组合键记入 `sessionStorage`，存储不可用时静默放弃增强且不阻断跳转，返回组合页聚焦并居中原行，进入产品页聚焦「返回组合详情」crumb。经底部导航离开产品详情时清理未消费的返回焦点键，避免后续访问组合时误聚焦旧行。移动产品详情保留底部全局导航，并由布局底栏净空 + 内容尾距保证末操作可达。
- 新增共享组件时默认双端可用；确需端侧独立组件时放 `components/mobile/` / `components/desktop/`，但仍消费同一套 token 与格式化函数。

## 7. 间距 / 圆角 / 阴影体系

- **圆角**：统一走 `--radius` 派生档位——卡片/对话框 `rounded-lg`、输入与按钮 `rounded-md`、badge/chip/状态点 `rounded-full`、checkbox `rounded-sm`（基件规格登记见 §13）；禁用 `rounded-[Npx]` 任意值。
- **阴影**：扁平金融风，**卡片一律无投影**，靠「页面底 → 白卡 → 边框」三级明度差分层；`Card` 基件已移除 `shadow-sm`，不再由消费页逐处覆盖。投影仅用于浮层例外——Toast / 下拉 / Popover 用 `shadow-lg`，模态遮罩不动卡片本体。
- **内边距**：卡片内容桌面 `p-6`（CardContent 默认）、移动 `p-3`；区块间距桌面 `space-y-6`/移动 `space-y-4` 为既有惯例，新页面沿用，禁用 `p-[Npx]`/`gap-[Npx]` 任意值。
- **z-index（现状登记，2026-08-29；#937 补窄例外）**：浮层统一 `z-50`（shadcn 基件默认：Dialog/AlertDialog 遮罩与内容、Select/Dropdown/Popover/Tooltip、移动端底部导航）；Toast 容器 `z-[100]`，须盖过模态层。**唯一新增非模态例外**：移动组合详情长页的「回到顶部」小按钮用 `z-40`，低于 Sheet/Dialog 的 `z-50` 遮罩，不抢点击或焦点；它不是整条底栏，也不引入新层级体系。除此例外与 Toast 外不自定义其他层级；新增浮层一律复用基件。
- **focus 态（现状登记，2026-08-29；#941 补净值图局部例外）**：走基件默认 `focus-visible:ring`（`ring-ring` = primary 靛蓝，§1.4；button/checkbox/input/switch/tabs 已带），不自定义 ring 宽度/offset；自绘可交互元素必须保留等价 focus 可见反馈。**净值折线图例外**只绑定 `NavCurve` 外层容器的 `nav-curve` 类，再限定其根 `svg.recharts-surface` 与内部 `g[tabindex]`：pointer/touch 聚焦时移除浏览器默认粗轮廓，键盘 `:focus-visible` 时用 `2px solid hsl(var(--ring))` + `2px` offset 保持可见；Recharts `accessibilityLayer`、根 SVG Tab 停靠与内部 `tabindex=-1` 分层均保留，内部层不新增 Tab 停靠点。不得把该规则扩成全站 `svg { outline: none }`，也不得用点击后强制 blur、关闭无障碍层或给卡片 `overflow-hidden` 替代；饼图、普通按钮与其它 SVG 仍按各自焦点约定。

## 8. 表格规范

- **数字列右对齐 + `number-cell`**（等宽 tabular-nums；份额/金额/净值与整数计数列一视同仁），文本列左对齐，操作列右对齐（`text-right`）；列表头与数据列对齐方式一致（数字列表头同样 `number-cell`）。禁止对数值单元格手写裸 `text-right` 或内联 `font-mono tabular-nums`（#249 起 ESLint 拦截）；需与其他类拼接时（如 `cn("text-muted-foreground", …)`）用 `lib/utils.ts` 的 `getNumberCellClass()`。
- **表头**：`text-muted-foreground` 常规字重，不加底色、不加粗（层级靠字号与文字色，不靠底纹）。**sticky 表头（#904 P1c）**：桌面 Table wrapper 限高内滚（`lg:max-h-[min(calc(100dvh_-_16rem),44rem)]`）+ `thead lg:sticky lg:top-0 lg:z-10 lg:bg-card`——实底为 sticky 功能必需（非色带），文字口径不变；`overflow-x:auto` 必成 scroll container、viewport 级 sticky 不可行，故桌面改内滚；移动保持页滚、thead 不 sticky（§6 端侧布局差异）。**已知边界（#920 L2 登记）**：`lg:bg-card` 预设 Table 处于白卡上下文；非卡上下文直挂 Table（现实例：`CashTransferListDialog` 的 `bg-background` 弹窗）lg+ 行数超限高时，吸顶表头白底浮于灰底弹窗、存在可见色差（暗色同理）；该场景消费方须覆写 thead 背景（如 `lg:bg-background`）；灰底直挂 Table 的三处消费方——`CashTransferListDialog`、`CashFlowsContent` 当日弹窗（`sameday-dialog`）与按平台构成弹窗（`breakdown-dialog`）——已同批覆写落地（#920 follow-up）；新增非卡上下文直挂 Table 的消费方须同批覆写。
- **斑马纹**：不用。行分隔靠 `border-b`，hover 行 `hover:bg-muted/50` 足够表达可点行。
- **空态**：表体空时表下方居中 `text-muted-foreground` 文案（或 `EmptyState` 组件），不渲染空表壳外的额外颜色。空态变体登记：① 数据空（默认）；② 筛选无结果——文案「无符合筛选条件的记录」+ 内嵌「重置筛选」入口（见 §9）；③ 无权限——文案「无权限访问本页」+ 返回入口按钮（`EmptyState` action 位）。
- **加载态**：表体区域居中 `Loader2 animate-spin` + `text-muted-foreground`，不清空表头。此为首次全量加载形态；筛选/翻页等局部刷新的加载态见 §14。
- **组合详情桌面持仓账表（#944 Phase 7.1）**：`PortfolioHoldings` 桌面「按产品」在 #915 资产大类分区与维度组合键子组内使用通栏表，列为「产品 / 市值 / 占比 / 累计收益 / 最新收益(MM/DD)」；分区头、子集 chip、子组头合计与 `?sub=` 语义不变，桌面不再使用卡片栅格或引导线缩进。产品列保留名称 + 显示码/市场双行与详情链接；现金不显示收益值。「按平台」使用「平台 / 市值 / 占比 / 持有收益 / 产品与现金」通栏表，平台合计置于组头，匿名平台不造死链。移动产品分隔线行、平台卡、平台详情产品卡与在途卡不变。表直接位于页面底色时，sticky 表头使用 `lg:bg-background`，不沿用白卡实底；组合详情内嵌账表取消管理表的桌面限高与 sticky 表头，随页面连续滚动，不形成多个独立纵向滚动区。
- **主次双行单元格**（#124 起）：单元格允许「主行 + 次要行」复合结构——主行 `text-sm` 正文色，次行 `text-xs text-muted-foreground`（§5/§1.4 既有档位，不新造）；适用场景登记：name + code、产品名 + 市场后缀。双端一致均渲染双行，移动端不裁剪次行。下拉选项内不用双行——用单行 `name (code)`（LOF 附市场后缀，如 `name (code · 场内)`），与既有表单下拉先例一致；#705 起括号内取**显示码**，港互认下拉只显 6 位销售码、不并列双码（下拉行宽有界，双码并列是详情页语义）。**同属「单行 `名称（码）` 标签」这一类的还有确认/编辑弹窗的产品名行**（`formatProductName()`：调仓确认 / 到账 / 编辑抽屉、份额事件确认 / 编辑），港互认同样只显销售码、不并列双码，是**有意取舍**而非漏改（#705 L2 评审 Suggestion 1 复核后维持）：① 复用 `formatProductCodeLabel()` 会拼出 `名称（968050（1001767344））` 的嵌套括号，另造 `名称（968050 / 1001767344）` 则是第三种码标签形态，与「并列与否由 `formatProductCodeLabel()` 单点决定」相冲；② 弹窗行是 `justify-between` 的左标签右值、移动端 390px 下值域已含长基金名，双码并列会挤压换行；③ 核对所需的机器码并未丢失——弹窗正是从列表行打开的，同一屏的 `ProductCell` 次行与详情页副行都是双码并列，#705「与 10 位码同时可见或明确标注」与待决第 3 条「界面某处保留完整原始码」由这些站点满足。若日后要改，需同时定新标签形态并做双端目检，不在展示码这轮内。**字重层级**（#355 登记）：产品列主行加 `font-medium`（产品是行内主标识），平台/投资人列主行不加粗——两者字重差是刻意层级、非漂移，勿「修平」。共用实现：`components/shared/NameCodeCell.tsx`（name + code）、`components/shared/ProductCell.tsx`（产品名 + `显示码 · 市场名`，#355；market 为空不拼后缀，避免 CASH/在途虚拟产品出现 `· --`）。**代码位取显示码而非机器码（#705）**：非港互认去掉 `.SH`/`.SZ`/`.OF` 后缀（`510300.SH` → `510300`），港互认显 `6位销售码（10位机器码）` 双码并列（`968050（1001767344）`，销售码缺失回落单显机器码）；行级/产品级有后端 `display_code` 时优先消费，缺失走 `lib/utils.ts` 的 `formatProductCodeLabel()` 单点兜底。**只有显示层换码**：href、`data-code`、React key、CLI 入参等标识位一律保持机器码。**次行布局判据**（#829）：代表性行的次行单行、不横向溢出、首列不窄于次行内容宽，由 `frontend/e2e/product-manage-detail.spec.ts` 的「产品名称合并列：代表性行次行单行、不溢出、首列不窄于内容（#829）」用例双端守门；调整 `ProductCell` 或产品表首列结构时同步跑该用例。此处只做指针，不是自动校验。
- **并列双行单元格**（#355 起）：语义**并列**（非主次）的两个同类值可上下双行，**两行同 `text-sm` 同正文色**，不做字号/色分层——区别于上条主次双行，两者是不同模式，按值间关系选用。顺序恒「**上行 = 先发生、下行 = 后发生**」，表头文案与行内上下顺序对应（如「交易/确认日期」「权益登记/除息日」）；每行挂原生 `title` 标注语义标签；任一行空值仍占位 `--`；两值相同（如场内当日确认 `trade_date == confirm_date`）仍渲染两行、不折叠，以保持列结构稳定；两行均 `whitespace-nowrap`，防窄列下日期按连字符断行。上下行槽位由调用点固定传入，**组件内不按值排序**（下行可能是 `--` 空值占位）。适用场景登记：交易/确认日期、申请/确认日期、权益登记/除息日。**例外**：pending 记录的确认日是预计值，在下行内联 `text-xs text-muted-foreground`「预计」后缀——合并后单元格已占两行，「预计」不能再起第三行。**移动流水卡可见标签（#907）**：卡片无表头、触屏无悬停，title-only 标签不可达，故移动卡经 `showLabels` prop 把语义标签渲染为可见前缀——每行 `flex items-baseline justify-between`，标签 `text-xs text-muted-foreground`（与 kv 格同档）+ 日期值 `text-sm`，「预计」后缀仍内联值侧；桌面表格不传 `showLabels`，仍 title-only 双行、输出逐字不变。共用实现 `components/shared/DatePairCell.tsx`。
- **结对行（父子行）**（#126 起）：成组记录（基金腿 + 现金腿）主行正常渲染；子行首列缩进一档（`pl-8`）、整行 `bg-muted/50`、内容降一档 `text-xs`（数值仍右对齐 `number-cell`）。主行去下边框（`border-b-0`）使主+子视觉成组，子行保留下边框作组分隔。操作按钮只在主行，子行不单独响应 hover/点击。子行文案模板：「现金扣款 · 平台名」/「现金到账 · 平台名」/「**现金待到账** · 平台名」（中点 `·` 分隔）；落单现金行：「现金 · 业务来源」（如「现金 · 申赎确认」）。**「现金待到账」的判据（#493）**：调仓卖出确认时会建一条 `confirmed` 但**到账日在未来**的 CASH buy 腿（`trade_date` = 基金确认日 C、`confirm_date` = 到账日 A，缺省 A=C）——主行状态讲的是**基金腿**的已确认，子行若照旧写「现金到账」会把尚未到账的钱说成已到账。故子行须按**现金腿自身的状态 + 生效日**判定：`confirmed` 且生效日不在未来才是「现金到账」，其余（未来到账、或到期却仍未确认）为「现金待到账」。**子行内容格（#493 起）**：金额之后空占位由 `colSpan={3}` 折叠，随后是**现金腿生效日**内容格（买入=扣款日 T、卖出=到账日 A），与主行「交易/确认日期」列对齐；末段再以 `colSpan={2}` 占满状态与操作列。**子行金额禁用涨跌色**（§1.1）——扣款 `-`、到账 `+` 符号表意，`text-foreground`；符号由展示层按 `trade_type` 推导，不回写数据。**子行空占位以 `colSpan` 折叠**（#355 起，frontend/ 内 `colSpan` 首次使用）：子行槽位与主行列数是纯位置耦合、tsc/lint 拦不住，连续空单元格折叠为 `<TableCell colSpan={n} />` 后形状一眼可核，span 写错会立刻视觉暴露而非静默错一列；改主行列数后须核对「首列标签 + Σspan + 内容格 == 主行列数」。**子行首列标签一律 `whitespace-nowrap`（2026-10-20，#732/#733）**：首列是全表最窄的一档（实测桌面 1280px 与移动 390px 下同为 129px，`pl-8` 缩进后只剩约 81px 文本宽），而 CJK 默认可在任意字间断行，「现金扣款 · 华宝证券」因此被切成 2（移动）～3（桌面）行、「↳ 买入 沪深300ETF（待确认）」3 行，平台名与产品名在词内断开；nowrap 后与本节既有的生效日、`份额 @ 净值` 同纪律，**双端同一口径、不分端**（横向滚动容器本就双端无条件存在，见 `components/ui/table.tsx`，不由 `variant` 决定）。代价是把断行换成横向滚动，实测可接受：桌面表宽 1017→1075px 仍落在容器内（滚动比恒 1.0，不新增滚动），移动滚动比 3.22→3.51（本就已滚动，增量不足三分之一屏）。**现金腿作主行**时份额与价格两槽同此语义——无份额无净值，一律传空值交回 formatter 出 `--`（§12），不得把后端记账单位 `price=1` 渲成 `1.0000`（#732）。
- **派生结对子行（对手腿被筛掉时）**（#719 起，「只补信息、不补行」的渲染端）：按产品/平台/状态筛选后，同组对手腿可能不在结果集里，此时用列表响应上的只读派生 `paired_leg`（口径见[业务规则·调仓](../reference/business-constraints.md#rule-trade)）**合成**子行。视觉形态与真实子行逐项一致，不新造第二套：同一副 10 列槽位算式 `1 + 2 + 1 + 3 + 1 + 2`（首列标签 + 平台·类型 + 金额 + 份额·手续费·价格 + 生效日 + 状态·操作）、`pl-8`、整行 `bg-muted/50`、`text-xs`、**无操作入口**、不响应点击。四条边界：
  - **不计入 `total`、不算一行数据**：它是主行的附属说明，翻页与计数契约不变；同组对手腿**已作为真实行**出现在本页时（判据 = `paired_leg.id` 在页内 id 集合中）**不渲染派生子行**，一组现金信息只出现一次。
  - **首列文案按对手腿类型分档**：对手是基金腿 → `↳ 买入/卖出 产品名（对手腿状态）`（产品名取后端 `product_name`/`display_code`，缺失走 `formatProductCodeLabel()`）；对手是另一条现金腿（跨平台转移）→ `↳ 转出/转入 平台名`，转入腿**尚未到账**时补 `（现金待到账）`（#493 口径：pending 或生效日在未来即未到账；已到账不带后缀，`（已确认）` 与主行状态列重复）；申赎腿（`sub_` 组）后端恒 `null` → 主行保持「现金 · 申赎确认」，**不补对手信息**（对手方是 subscription 记录，主体在申赎页）——刻意边界。`↳` 前缀标明「这是另一条腿」而非本行的现金子记录。**派生 pair 的子行一律走这一档**，含对手是现金腿的平台间转移（#721 评审 S1）——`现金扣款/现金到账 ±` 那套模板只用于结果集里的**真实现金腿**子行，两套形态不因筛选而互换。
  - **主行产品列不换**：派生只**加子行**，主行本身是现金腿时产品列仍是 `cashOrphanLabel` 的业务来源标注（「现金 · 调仓」等），不改成 `现金类资产` 的 ProductCell——筛现金要读的是资金去向，不是产品名（接线在 `TradesContent` 的 `keepCashSourceLabel`，仅派生 pair 生效；真实转移对「双 CASH，sell 为主」形态照旧）。
  - **金额不加 `+`/`-` 前缀**：前缀是资金流向语义、只属于现金子行；派生子行的金额格是对手**基金腿的 `amount`**（买入=扣费后净投入、卖出=成交额，#173/#182），与主行现金腿的实扣实到是两套口径，不并存、不互相冒充。份额·手续费·价格合并槽位（`colSpan={3}` `text-right`）只在对手是基金腿时填 `N 份 @ 净值 · 费 ¥X`；现金腿无份额无净值语义，该槽位留空。
  - **生效日槽位**：对手腿为卖出方向时前缀「到账」（与 #493 现金子行同口径），其余直接显日期；`confirm_date` 缺失时**留空**，沿用 #493 现金子行的既有形态——这是 §12「空值占位统一 `--`」在子行补充槽位上的既存先例登记（真实数据的 `trade.confirm_date` 创建即按 `confirm_days` 定值，缺失只出现在异常存量），不是新的空值口径，勿据此把主行数据格的 `--` 改成留空。
  - 共用实现：文案与判据单点在 `lib/tradePairs.ts`（`cashRowLabel` / `counterpartSubLabel` / `counterpartDateText` / `tradeCardDetailLine`），三处同形交易卡（`ProductDetailContent` / `PlatformDetailContent` / `PlatformProductDetailContent`）与交易列表都从这里取——现金行的副行曾各写一份 `shares !== undefined` 判据，把现金腿渲成 `-- @ 1.0000`（虚假净值暗示净值型产品），收敛到单点后由 `tradePairs.test.ts` 钉住。
- **多行合并段（rowSpan 组列 + 余额列）**（#725 起，现金流水页桌面 7 列首个落地）：相邻同 `(平台, 现金日期)` 的多条流水属**同一平台同一日**的一段，首列「日期 · 平台」与第 5 列「当日余额」各以 `rowSpan={段内行数}` 纵向合并成一格——余额是**日终存量**、整段共用一个值，逐行重复既冗余又会被读成「每笔都改变了余额」。分组键与跨度由 `lib/cashFlows.ts` 的纯函数单点决定（`cashFlowGroupKey()` = `` `${platform_code}|${cash_date}` ``、`balanceSpans()` 线性扫描、被覆盖行返回 `-1`），组件只消费不另算。合并格按跨度取垂直对齐——`rowSpan>1` 的合并段在段内垂直居中（`align-middle`），筛选打散退化为单行（`span=1`）时与逐行列一致顶对齐（`align-top`）；非合并列（流水类型 / 来源·去向 / 金额 / 状态 / 操作）逐行照常 `align-top` 渲染。**跨度按屏上实际行数算**，故按流水类型/状态筛选把一段打散后自然退化为多个单行格（span=1），不会错位；也因此**余额值与筛选无关**——它取的是后端派生的日终余额，不是屏上金额累加。列数核对仍走 #355 口径：改主行列数后核对「组列 + 4 常规列 + 余额列 + 操作列 == 7」。**移动端不合并**：卡片流每卡自带「平台 · 日期」与余额，重复是可接受的（纵向滚动无表头列对齐约束），不搬 rowSpan。共用实现 `components/shared/CashFlowsContent.tsx`。
- **操作列控件形态**（#823 起）：列表行操作统一 `ghost + size="sm"` 图标按钮（图标 `h-4 w-4`，档位见 §16），经共享 `RowActionButton` 渲染，禁止再手写同功能件；存量手写列表按页面改动时收敛，不一次性迁移（首批落地：现金流/调仓/份额事件三页；#846 维护者授权的显式排期例外：产品/平台/投资人/申赎/快照/资产分类六页同批收敛）。`title` 与 `aria-label` 必须同值且为短词（如详情/编辑/确认/取消/取消确认/删除/修改/修改到账日期）；**唯一例外是禁用原因的长解释**——按钮禁用时 `title` 可承载原因（如快照「仅可删除最新日快照」），`aria-label` 仍为短词「删除」（#846）。同一语义动作跨页必须同图标同文案（详情 = `Info`）。危险操作（删除/撤销/取消/取消确认）必须 `text-destructive`——icon-only 按钮以图标形状 + `title`/`aria-label` 文本作非颜色通道，为 §1.1 决策 2「绛红伴随文字」的显式例外；二次确认沿用 §14 既有弹窗，迁移时不新增、不移除（#845 澄清：该句为迁移纪律，不禁止按 §14 补齐缺失的二次确认）。**文本例外**：状态推进类且 label 为动态业务文案的动作（当前实例：现金流「确认到账」）允许 `outline + sm` 文本 + pending `Loader2` spinner；静态短词动作禁止退回文本链接。触控目标沿用 `h-9`（36px，满足 WCAG 2.5.8 AA 24px 下限），双端同尺寸、不为移动端单独放大。
- **「更多」菜单例外（#856 登记）**：产品管理页（`ProductsContent`）行操作收敛为单个 `ghost + size="sm"` 的 `MoreHorizontal` 触发按钮（`title`=`aria-label`="更多操作"，图标 `h-4 w-4`），点击展开 `DropdownMenu`——菜单内 编辑（`Pencil`）/ `DropdownMenuSeparator` / 删除（`Trash2`，`text-destructive`；`ui/dropdown-menu` 无 destructive variant，故以 className 承载）。同步价格/历史/走势入口迁至净值型产品详情页（`ProductManageDetailContent`），列表行不再挂多个图标按钮。此为 #823/#846 `RowActionButton` 规范的显式例外：行内动作收敛进菜单时，触发按钮本身仍守 ghost+sm+图标档位与 title/aria-label 同值短词纪律，触控目标取 `h-8 w-8`（32px 方形 icon-only，仍满足 WCAG 2.5.8 AA 24px 下限），为 §8 `h-9` 通用触控档的显式尺寸豁免；菜单内危险操作仍 `text-destructive` + 既有 `ConfirmDialog` 二次确认（不新增、不移除）。已停用产品名称列**主行内联** `Badge variant="neutral"`「已停用」（`ProductCell inactive` prop，启用态不渲染、输出与原先一致），不改次行布局判据（#829）。详情页三动作（编辑/停用启用/删除）为页面级动作、不在本表格操作列规范内；#935 起从页眉移入正文身份区下方（`data-testid="product-manage-actions"`），双端编辑/启停图标+文字、删除独立图标（aria-label 表意、危险色），随正文滚动。
- **管理页移动卡片形态（#897 起）**：投资人/平台两管理页移动按 §6② 走卡片列表（共享组件 `variant` 分支，与资产分类卡同形：`rounded-lg border p-3 space-y-2`、无投影）：投资人卡标题行 = 姓名 + 角色 Badge + 右侧 `RowActionButton`（编辑 / 重置密码（仅他人行；本人行沿用 #898 判据不渲染）/ 删除，危险色与二次确认沿用），信息行 `用户名：/电话：/邮箱：` 带标签同口径（`text-xs text-muted-foreground`）；平台卡标题行 = 名称 + 操作，次行 `代码 · 类型`。空值 `--`（§12），字段集合双端一致（§6）。**邮箱允许换两行**（`break-all` 兜底防溢出，不加 nowrap、不做单行断言；维护者 2026-11-17 定案）；用户名行与平台「代码 · 类型」次行同加 `break-all`（#901 L2 Suggestion：无断行长串实测溢出卡片后修，与邮箱同口径）。两页 `CardHeader`（「投资人列表/平台列表」大标题 + 描述文案）**整块仅移动不渲染**、桌面保留（维护者 2026-11-17 13:08 定案「去大标题、排版布局统一」；其中描述文案收掉是**经 2026-11-17 维护者定案的单点例外（非裁字段）**——不属 §6④ 装饰性层级线索类，#901 L2 Nit 纠正措辞）。桌面 6 列/4 列表格结构不变。双端门禁：`frontend/e2e/admin-mobile-cards.spec.ts`（mobile 断卡片容器存在 + table 不存在 + 姓名/用户名/平台名称/代码·类型单行 + 大标题/描述不渲染 + 重置入口他人卡有/本人卡无/dialog 实开；chromium 断表头列结构与 CardHeader 不变，行集锚点顺序无关），单行口径复用 `expectSingleLine`（#853）。
- **产品管理列表 / 组合投资人 Tab 移动卡化（#904 P1d）**：上条 §6② 卡化扩至两处带批量/派生列的列表，共享组件 `variant` 分支、卡形同上条（`rounded-lg border bg-card p-3 space-y-2`）。**产品卡**（`ProductsContent`，容器 testid `products-card-list`、每卡 `product-card`）：标题行 = 批量勾选 Checkbox + `ProductCell` 名称单元格（`product-name-cell`/`product-detail-link`/`product-cell-meta` testid 与链接判据保留）+ 「更多」菜单——菜单与桌面表格行经 `rowMenu` 单点同源（#856 条款对卡内菜单同等适用）；kv 栅格 2 列 = 类型/确认天数/估值滞后/QDII/数据源，字段集合与桌面列一致（§6）。**全选**自表头移卡列顶行（`batch-select-all`，文案「本页全选」），批量多选/禁选（虚拟产品、在途、上限钳制）逻辑与桌面逐字同源。**投资人卡**（`PortfolioInvestorsList`，容器 testid `portfolio-investors-cards`）：标题行 = `NameCodeCell` + 已清仓 Badge；kv 8 字段与桌面九列一致（份额/占比/市值/总成本/成本价/冻结/持仓收益（主值+百分比副行）/累计收益*），行 testid（`investor-row`/`investor-row-liquidated`）与 `investor-ratio`/`investor-cumulative` 保留；移动横滚壳 `min-w-[900px]` 随卡化退役；口径注记 `investor-cumulative-note` 在形态分支外恒渲染（双形态同口径，§12）。**kv 单元格形态**：标签为独立块级 `span`（`block text-xs text-muted-foreground`）、值为 `b` 左对齐——数值字段经 `NUM_CELL = cn(getNumberCellClass(), "whitespace-nowrap")` 再拼 `"block text-left text-sm font-normal"`（nowrap 防窄列下数值与 CJK 表头折行/竖排，#394/#355 同族；与 §5 统计卡左对齐口径同源）。**已知边界（#922 L2 登记）**：kv 栅格 `grid-cols-2` 为 minmax(0,1fr) 不撑轨，375px 视口每列约 157px，常见 8-9 位份额/市值可容；极端长值（千亿级份额、两位数亿级市值）有溢出列轨道的余量风险，未做真实大数据目检，后续如出现再核。标签独立成元素使 E2E `getByText` exact 可直命中标签而不含值文本。桌面表格结构不变。双端门禁：`product-batch-data-source` / `product-manage-detail` / `product-data-source` / `portfolio-investors` 四 spec 行定位走 `.or` 双形态单定位（表格 row `.or` `product-card`；`portfolio-investors-table` `.or` `portfolio-investors-cards`），表头类断言按 project 分流（mobile 改断 kv 标签 exact）。
- **流水页移动卡化（#907，流水卡解剖单点 `FlowCard.tsx`）**：调仓 / 申赎 / 份额变动事件 / 快照四页（`TradesContent` / `SubscriptionsContent` / `ShareChangeEventsContent` / `SnapshotsContent`）移动按 §6② 改流水卡列表，共享组件 `variant` 分支，**桌面表格逐字零变化**（行操作矩阵抽成共享 render helper，两端渲染同一 `RowActionButton` 集合）。卡解剖由 `components/shared/FlowCard.tsx` 单点持有（§13），承 #897 卡形（`rounded-lg border p-3 space-y-2`、无投影 §7）：`FlowCardHeader`（左 `title` 名称 `text-sm font-medium` 长名可换行 + `meta` `code · 市场 · 平台` 次行 `whitespace-nowrap text-xs text-muted-foreground`；右 `aside` 方向/类型 neutral chip + 状态 Badge，`shrink-0` 不被名称挤压）→ `FlowCardKVGrid`（`grid-cols-2`，每格 `flex justify-between`：标签 `text-xs text-muted-foreground` + 值 `number-cell text-sm`，**testId 挂值 span 而非整格**——含标签会污染 E2E 文本精确断言）→ 日期行（复用 `DatePairCell` 并传 `showLabels`：每行 `flex justify-between` 渲染可见标签 `text-xs text-muted-foreground`（与 kv 格同档）+ 日期值 `text-sm`，pending 确认日「预计」后缀同上条并列双行；桌面表格不传 `showLabels`、仍 title-only 双行零变化，详见上条 #355 移动卡例外）→ `FlowCardActions`（右对齐 `RowActionButton`，**仅主行**）。**字段集合与桌面数值列一致**（§6，不裁字段）：调仓常显金额、核对明细含份额/手续费/价格，申赎常显金额、核对明细含份额/净值，事件常显份额变化/现金变化，快照常显市值/在途、核对明细含净值/份额；空值 `--`（§12），现金腿份额/价格传 null 不渲 `1.0000`（#732 同口径）。**结对子块**（`FlowCardSubBlock`，`bg-muted/50 text-xs`、标签 `whitespace-nowrap`、**无操作**）：调仓结对折进一卡，现金腿/派生对手腿是卡内子块而非独立行，子块 testid（`trade-cash-subrow` / `trade-counterpart-subrow`）双端保留，文案与判据仍走 `lib/tradePairs.ts` 单点（本节结对行/派生子行条款对卡内子块同等适用）。**快照卡**：标题 = 快照日，`aside` = 涨跌 neutral Badge（`getSignedReturn` 着色文本），删除仅最新日可用、非最新日 `disabledHint`「快照连续原则：仅可删除最新日快照」（#846 长解释例外）。**事件卡**：现金到账日独立行（testid `event-cash-pay-date`，null 注「默认除息日 {除息日}」），份额变化/现金变化值 span 挂 `event-shares-change`/`event-cash-change`（pending 自动计算型恒 NULL → `--`，#424）。**与 #897 管理页不同：流水页保留 `Card`/`CardHeader`**（标题/描述不收起）。容器 testid `trade-card-list`/`subscription-card-list`/`event-card-list`/`snapshot-card-list`，每卡 `trade-card`/`subscription-card`/`share-event-{id}`/`snapshot-card`（事件行 testid 双端一致）。双端门禁：`admin-mobile-cards.spec.ts` 扩四页（mobile 断卡容器在 + table 不在 + kv 字段集合 + 日期行可见标签；chromium 断表头列结构不变 + 卡容器不在，**不做 CardHeader-gone 断言**）；流水 spec 行定位走双形态 `table tbody tr, [data-testid="trade-card"]`（事件走 `share-event-{id}`），表头/cell 类断言按 project 分流。
- **移动流水渐进核对明细（#944 Phase 8.1，D1/A8）**：`FlowCardDetails` 是移动流水卡共用展开件，触发器文案「展开核对明细/收起核对明细」，高 44px，具 `aria-expanded` / `aria-controls`，Enter/Space 可切换；明细默认收起，常显身份、方向/类型、金额或市值、状态、关键日期与现金腿。展开内容位于触发器之后，不推动触发器离开原阅读位置；组件按稳定记录 key 保留状态，局部刷新不无故折叠。份额事件的两项字段均属核心金额，不设空展开器。桌面表格不消费该件，字段与列结构不变。双端门禁：`mobile-flow-detail.spec.ts` 验证键盘、触控高度、阅读位置、刷新保持与桌面无卡片；`admin-mobile-cards.spec.ts` 验证四页常显/明细字段。

## 9. 筛选栏规范（filter bar）

> 流水类列表页（申赎/调仓/快照等）的标准配置，全站首个落地为 #125/#126。本节只定跨页一致决策，控件本身读 `components/ui/`。

- **容器**：置于表格上方、与表格同卡片内容区顶部，不单独卡片包裹；横向 `flex flex-wrap`、控件间距 `gap-2`，与下方表格的间距走 §7 区块档位。
- **控件尺寸**：筛选栏控件桌面统一紧凑档 `h-9`（Select/Input/日期触发按钮同高）；表单对话框内仍为 `h-10`，两档不混用。**移动 sheet 内控件 `h-10`（40px 触控档，#904 P1b 落地）**；桌面 `h-9` 不变。本款覆盖 Select/Input/日期触发按钮；sheet footer 动作按钮与图标按钮按 44px（`h-11`/`h-11 w-11`，#917 L2 工效补充）。仓库触控基线为 §8 WCAG 2.5.8 AA 24px 下限，44px 为建议值。
- **标签**：筛选栏省略 `Label`，以 placeholder 表意；placeholder 统一「全部 + 维度名」（全部状态/全部平台/全部产品）。默认有值的筛选（如"最近 1 年"）显示实际值而非 placeholder。
- **控件排序**：全站统一——时间区间 → 状态 → 实体维度（投资人/平台/产品）→ 类型；多页并存时顺序一致。
- **生效方式**：变更即时查询，不设「查询」按钮；文本输入类防抖 300ms，下拉/日期选择即时生效。
- **重置**：单项清空用控件自带清除件（如 date-picker 的 X 先例）；存在非默认筛选时，筛选行末尾出现「重置」ghost 按钮，点击恢复默认筛选集。
- **激活提示**：桌面端控件值即提示，不额外加徽标；移动端「筛选」入口按钮带激活计数 Badge（`default` variant——纯计数无状态语义，§1.3）。
- **移动端形态**：筛选控件收进「筛选」底部 sheet（#904 P1b，`ui/sheet`；触发按钮带激活计数 Badge），展开后纵向堆叠 `grid-cols-1`、控件 `h-10`；footer sticky「重置/完成」，完成仅关闭 sheet，变更即时查询语义不变。
- **筛选无结果空态**：见 §8 空态变体②——文案「无符合筛选条件的记录」+ 内嵌「重置筛选」入口。

## 10. 日期区间选择器（date-range-picker）

> #125 新建 `components/ui/date-range-picker.tsx`（Calendar `mode="range"`），#126 复用。单选 DatePicker 已定先例（outline 触发按钮 + CalendarIcon + 占位 `text-muted-foreground` + X 清空 + 交易日标注）继续有效，本节只定区间场景的新增决策。

- **触发按钮**：继承单选先例；区间文案 `YYYY-MM-DD ~ YYYY-MM-DD`（§12），移动端不换行、溢出省略；桌面端截断时悬停可见完整文案（原生 `title`）。
- **快捷选项**：桌面置日历左侧竖排文本按钮列表，移动端置顶部 `flex-wrap` 换行完整显示（#154 修订：横向滚动在 Popover 内被 flex 布局撑破失效，换行为确定性形态）；选中态 `bg-success-soft text-success-foreground font-medium`（primary 同源 soft 底，§1.2）。快捷项清单由业务定义（如本月/最近 1 年），规范只管形态。联动规则：手动改动区间后，与某快捷项区间完全一致则保持其选中，否则解除全部快捷项选中态。
- **区间选中配色**：起止日 solid `bg-primary` 白字，中间区间底 `bg-success-soft`——不加新 token（primary = success 同源，§1.1 决策 3）。
- **弹层行为**（#154 修订）：弹层内选择为**草稿态**（手选与快捷选项均只填草稿、不关弹层），底部 footer 显示草稿摘要（`起 ~ 止 · 共 N 天`），点「确定」提交并关闭；点弹层外区域关闭 = 放弃草稿。单日区间 = 首击同一日即得 `{D,D}` 草稿，确定提交；草稿态再点同一日 = 清空草稿（react-day-picker v10 `addToRange` 语义）。清空只靠触发按钮 X，弹层内不重复造清空件。弹层总高以视口为限（`max-h` 取 `min(100dvh − 2rem, 44rem)`）：超高时弹层**内部滚动**，footer（摘要 + 确定）sticky 常驻底部，任何视口下「确定」可达；桌面双月并排（`sm:flex-row`，窄屏 flex-wrap 回落纵排）（#161）。
- **双端**：桌面 `numberOfMonths={2}` 双月并排，移动 `numberOfMonths={1}` 单月。（并排于 #161 落实：Calendar `months` 样式 `sm:flex-row`，此前代码纵排为定制偏差）
- **交易日标注边界**：筛选场景**不启用** `showTradingDays`（历史区间无交易日语义）；交易/事件录入场景仍用单选 DatePicker 并标注交易日。

## 11. 分页规范

> 随筛选栏引入（#125/#126），此前全站列表均为单页全量拉取。翻页/筛选触发的局部加载态见 §14。

- **桌面形态**：页码列表——总页数 ≤7 全显，>7 折叠为「首末页 + 当前页 ±1 + 省略号」；右侧每页条数切换（20/50/100，默认 20）。
- **移动端形态**：简化为「上一页 / 第 x / N 页 / 下一页」，无页码列表与条数切换。
- **总数**：「共 N 条」`text-xs text-muted-foreground`，置于分页控件左侧。
- **位置**：表格下方整行右对齐（与操作列对齐惯例一致，§8）。

## 12. 文案格式惯例

- **日期**：统一 `YYYY-MM-DD`（`formatDate`）。日志/执行历史等按时间倒序的同日多条表用 `YYYY-MM-DD HH:mm`（`formatDateTime`，#715 补回）；相对时间（今天、N 天前）当前无消费方。
- **百分比**：自带 `+/-` 符号（`formatPercent`/`formatReturnRate` 默认 showSign），禁止手工拼 `+`；负号由数值自带。
- **组合详情页头快照日期**（#943 S3）：净值型组合 meta 为「组合代码 · 最新快照日期 YYYY-MM-DD」，closed 仍显示最新历史快照；无快照（含 draft）日期显示 `--`，台账型只显示组合代码。建立日期不再出现在详情页头，统计卡下方不得重复显示快照日期；页头 sticky 折叠时 meta 随既有规则隐藏。
- **组合统计卡两项百分比标签**（#943 S3）：总资产卡只挂累计收益率，净值卡只挂最新快照相对前序快照的日涨跌率；两者均固定在主数值第二行并左对齐，不因桌面卡片剩余宽度回排到数值右侧，均用 `formatReturnRate` 与既有涨跌 soft 色，可访问名分别「累计收益率 {值}」「快照日涨跌率 {值}」。日期、净值与日涨跌必须来自同一份 `snapshotApi.list(code, { limit: 2 })` 响应；列表与状态查询须同轮原子提交。仅当两条快照日期严格有序、净值有限且前序净值为正、`missing_dates` 在两条快照之间无缺口、status 的最新快照日与列表首条一致时显示日涨跌，否则整个标签不渲染。无快照、单快照、断档、台账型、draft 与查询未完成都不得伪造 `0.00%`；计算得到的真实零显示 `0.00%`。draft/台账不发起这两项快照读取；快照列表或状态查询失败走共享 `QueryErrorState`，不得落成无快照空态。首次失败，以及已有数据后的后台刷新失败（含空快照），均保留组合详情和管理操作，在快照派生数据上方显示内联重试；失败期间隐藏日涨跌，重试成功后恢复。
- **金额**：带 `¥`、千分位、2 位小数；概览大字可用 `formatCompactCurrency` 的万/亿紧凑格式（`¥X.XX 万` / `¥X.XX 亿`），表格内不用紧凑格式。
- **空值占位**：统一 `--`（各 format 函数 fallback），禁止 `N/A`、`null`、空字符串上屏；JSX 内禁止手写 `-`/`"--"` 字面量当占位——空值判断交回 format 函数（`null`/`undefined`/空串/`NaN` → fallback `--`），不要用 truthy 三元短路（真 0 会被误判为缺失，如 `fee=0` 应显示 `¥0.00`；#249 起 ESLint 拦截 JSX 内 `-`）。
- **单位**：份额数值后带「份」（一律 `formatSharesUnit`，见 §3）、金额不重复写「元」（`¥` 已表意，例外见下条）；图表 tooltip 中金额可带「元」补语义。
- **概览大字「数字 + 元」例外**（#249 登记，设计来源 #99/#114/#117）：概览大字与分区/分组合计允许 `formatNumber(...)` + 手写「元」后缀（适用清单：`PortfolioHoldings` 大类分区头合计/维度·组合键 chip 合计（#915）、`PortfolioStatsCards` 统计卡），大字区「数字 + 元」比 `¥` 更紧凑、不与表格内 `¥` 争视觉层级。**边界**：仅覆盖上列概览大字与分区/分组合计；表格数值单元格、卡片小字明细不在例外内，仍走 §3 format 函数（收益列的无 `¥` 形态见下条 #595 例外）。**规范与实现二选一**：若未来决定代码侧统一为 `formatCurrency`，须先撤回本条例外，不允许两者并存。
- **持仓产品收益列无 `¥` / 平台卡现金段 0 位小数例外**（#595 登记，#944 Phase 7.1 扩至桌面账表）：`HoldingProductCard` 的移动 row3 **两列**与桌面账表收益列（累计收益 | 最新收益(MM/DD)）按设计稿为纯数字带符号（`+980.00`，正负号展示层拼、数值走 `formatNumber` 系），同卡/账表市值仍带 `¥`；**row3 收益口径（#944 V2-16）**：「累计收益」使用 by-product 的 `cumulative_profit`（全历史净现金流口径，含已卖出实现盈亏），不再使用当前持仓的 `holding_profit`；标题旁 `InfoTip` 默认收起，展开文案为「累计收益含已卖出实现盈亏」。`HoldingPlatformCard` 的「持有收益」仍保持持有口径，不随本条切换；`HoldingPlatformCard`「N 只产品 · 现金」段按设计稿大额取 0 位小数（`¥40,000`），负现金为真实存量、不省略且符号内显。**row3 列数（2026-10-20，#724）**：#595 设计稿 M1/D1 定义的是三列（持有份额 | 累计收益 | 最新收益），其中的「持有份额」列已**双端移除**——390px 下三列等分每列仅 ≈93px，带日期的标题折行使该列数值下沉、份额被 `truncate` 成 `53,178.1…`（金融数值不可 ellipsis，#636）；份额由详情页概览卡的「持有份额」条目承载、产品卡整卡可点即达，信息未丢——两个消费者各自的去向：组合「按产品」视图产品卡 → `ProductDetailContent.tsx::overview`（跨平台聚合份额）；平台详情页产品卡 → `PlatformProductDetailContent.tsx::overview`（本平台切片份额）。移动两列数值仍用 `tabular-nums` 而非 `number-cell`，理由见 §3 漂移台账；桌面账表按 §8 使用 `number-cell`。**row3 收益值不截断（2026-10-20，#724 L2 评审收敛）**：两列收益值已去 `truncate` 改 `whitespace-nowrap`（#636 纪律：金融数值不可 ellipsis）——半列实测移动 390px 档 146px、桌面 1440 档 160px（#741 隔离栈探针取数）；`HoldingPlatformCard` 持有收益段的 `truncate` 为既存同类面、本次未触及，触及时对齐。**溢出代价形态（2026-10-21，#741 第二轮登记）**：两列由 `flex-1`（basis 0%）恒等分、row3 容器无 `gap`，去 `truncate` 后既无 `overflow:hidden` 也不出省略号——第一列超宽会与邻列文字**直接叠印**、第二列超宽会**溢出卡片边框**（移动页壳无 `overflow-x` 裁切，理论可致整页横移）。叠印/溢出是本次接受的代价（比省略号吞掉数值更可读），触发阈值未实测、个人记账存量远未及；出现超宽存量时按本条复核，不据「已登记可接受」判定无需处理。**边界**：覆盖移动产品行两列与桌面账表「累计收益 / 最新收益」两列；其余金额展示仍走 §3 format 函数（2 位小数带 `¥`）。**规范与实现二选一**：本条例外现覆盖三项决策——① 收益列无 `¥`/无 2 位小数口径；② row3 为两列（设计稿 M1/D1 定义三列）；③ row3 收益值不截断。改回任一项（含「照 Ardot 画布把份额列补回卡片」）都须先撤回对应登记，并同步两处守卫断言：`frontend/e2e/portfolio-holdings-view.spec.ts`（两列标题同行同高、`total-profit-value` 取 `cumulative_profit` 且同源涨跌色、累计收益 `InfoTip` 同口径、`not.toContainText('持有份额')`、收益值 `text-overflow: clip`）与 `frontend/e2e/product-detail.spec.ts`（概览「持有份额」正面锚），不允许文档与实现并存两套口径。
- **持仓产品整卡/整行链接的选中代价（#944 Phase 6.1/6.2）**：产品卡与移动分隔线行使用独立覆盖链接承载跳转，内容层通过 `pointer-events-none` 让指针落到链接，InfoTip 触点显式恢复 `pointer-events-auto` 与局部层级。此形态不把链接嵌套进说明按钮，但卡内名称、代码与金额文本不能由鼠标/触摸直接选中；这是保留整卡/整行点击与避免嵌套交互的明确取舍，不通过缩小触点或删除说明恢复选中。移动行链接用机器码 `data-product-code` 与 code-market 全键 `data-product-key` 标识，按组合写入/消费/清除 `sessionStorage` 返回焦点；写入/读取/清理均在存储不可用时降级，不阻断跳转或持仓渲染。产品详情首屏聚焦「返回组合详情」crumb，底栏不遮挡末尾操作。
- **现金流水页金额符号三档例外**（#725 登记，设计来源 mockup `fmtMoney`/`fmtNumber`；#727 评审对齐实现）：现金流水页按「存量 / 流量 / 对账明细」分三档取符号与 `¥`，前两者由 `lib/cashFlows.ts` 的纯函数单点持有、第三者复用既有函数，均建在 `formatCurrency`/`formatNumber` 上（不散装 `toFixed`）——① **存量**（可用现金、在途、平台日终余额、构成表余额列、对账块与投影块的期初/日终/合计行）走 `formatBalanceCurrency`：**负号在 `¥` 前**（`-¥1,234.00`）、正数**不补 `+`**（存量是「有多少」不是「涨跌」，`+` 会误读成收益）、空值 fallback `--`；② **流量**（本期流入/流出、金额列、构成表流入流出列）复用 `lib/utils.ts` 既有的 `formatSignedCurrency`（现金流水页不另造同名函数）：**± 在 `¥` 前**、正数补 `+`（`+¥5,000.00` / `-¥3,200.00`），与 #638 L2 S2「正负号不得分居 ¥ 两侧」同源；金额列有一个例外——**重估行**（`is_override`，绝对值覆盖不是增量）走 ①（`cashFlowAmountText()` 单点分派），带 `+` 会把「余额被覆盖为 X」误读成「流入 X」；③ **对账明细裸数字**（对账块内逐笔「当日已生效」行、投影块「本笔到账」行）走 `formatBareSigned`：**带 ± 不带 `¥`**（`+5,000.00`），对齐 mockup 明细行右对齐的紧凑列——只覆盖这两类**增量明细行**，块内的期初/日终/合计是存量、仍走 ①。**边界**：仅覆盖现金流水页；存量档的「正数不补 `+`」是本页相对全站 `formatSignedCurrency` 的**唯一新增取舍**（存量非涨跌），其余两档都复用既有惯例。**规范与实现二选一**：若未来统一为全站 `formatCurrency`/`formatSignedCurrency`，须先撤回本条例外。
- **净值口径回退的屏上标注**（#703 登记）：产品详情页与平台-产品详情页的净值曲线卡与区间收益率卡标题改由响应字段 `nav_basis` 驱动——`accumulated` 维持原文案（「累计净值走势」/「区间收益率」），`unit` 在净值源改「单位净值走势」、在场内改「收盘价走势（市价）」/「区间收益率（市价）」；回退口径下卡标题后加一行 `text-xs text-muted-foreground` 注记（形态同 #641 脚注），写明不含分红再投资、场内为市价含折溢价。文案单点在 `src/lib/navBasis.ts`（两页共用、防副本漂移，colocated vitest 钉标题与注记）。**边界（失效条件）**：空态与失败态判序不变——曲线与六窗皆空才落「暂无净值数据」+ 全「--」，此时仍报 `accumulated`、不加注记；`aria-busy` 局部加载态与失败臂位置均不动。E2E 双页同批改（`product-detail.spec.ts` 三条：回退口径、皆空空态、累计净值口径不加注记；`platform-detail.spec.ts` 一条同形），双端各跑。
- **口径注记分色**（#595 登记，#943 S2 修订）：带 `*` 的口径说明注记是「口径与直觉不同」的警示，走 `text-xs text-warning`（琥珀警示语义）；有数据时恒渲染，空态走「暂无」分支不渲染。`PortfolioInvestorsList` 投资人累计收益注记继续沿用该形态。**产品详情例外**：`ProductDetailContent` 与 `PlatformProductDetailContent` 的累计收益说明自 #943 S2 起改为相邻 `InfoTip`，标签不再带 `*`、正文不再常显，展开后保留完整口径文案；`HoldingProductCard` row3「累计收益」自 #944 V2-16 起也使用同一相邻 `InfoTip`，与两处详情同 basis；这不是全局撤销口径警示。**区别于中性口径脚注**（#641 在途脚注、上款 #703 净值回退注记）——纯口径补充、无警示语义的一律 `text-xs text-muted-foreground`，两种注记不混用。
- **平台详情页持仓明细区的在途口径脚注**（#641 登记）：`*持仓市值含在途资金 ¥N，上方卡片不含在途`（`text-xs text-muted-foreground`，明细网格后），仅本平台在途 > 0 时渲染；数字必须取该平台 by-platform 卡的平台级 `in_transit_market_value`（无在途为 0.0），**不得借组合级字段**——多平台组合会把 A 平台的在途写到 B 平台名下（PR #639 B2）。E2E 见 `platform-detail.spec.ts` 在途脚注用例。

## 13. 组件复用红线

- 新 UI **先查 `components/ui/`（基件）与 `components/shared/`（业务件）**，能复用不新造；状态徽标一律 `Badge` + variant（#127 的 9 处手写 badge 收敛为首个执行案例），禁止再出现 `inline-flex items-center rounded-full … bg-*-100 text-*-800` 手写件。
- **页头单点 = `components/shared/PageHeader.tsx`**（#904，#935 修订）：面包屑 / 标题+badge / meta / 操作四槽；新页禁止手写页头 flex 组合。标题走 §5 页面标题档 **20px/28px，展开/折叠一致**；面包屑/返回文字 14px/22px（#935）。**三类产品详情（产品管理/组合内产品/平台-产品）只传短标题「产品详情」**，完整产品识别由正文 `ProductIdentity` 承载（下条），不再把长基金名放页眉；`truncateTitle` prop 保留，供平台详情等其他长标题实体页使用（组合名等短标题不压缩 #786）。badge 仅非默认态挂载；操作权重三档：语境入口 ghost 链接档 → 次动作 outline → 主动作 filled 最右。**sticky 折叠（P1c，#935 S3）**：viewport 吸顶 top-0（负 margin bleed 到 main padding 边缘、bg-background），滚动 >32px 折叠——面包屑隐去、首 crumb 收标题行内图标返回（aria 不变）、meta 隐去、出 border-b；**标题不随滚动改字号**；即时切换不做尺寸动画（§14）。三类产品详情不向页眉传 badge/meta/actions，折叠页眉因此只剩返回 +「产品详情」——产品身份随正文滚走是已确认取舍（issue #935 S3），其他页面的 badge/actions/meta 显示逻辑不变。
- **组合详情操作组单点 = `components/shared/PortfolioActionButtons.tsx`**（#943 S4）：桌面与移动 `/m` 共用 `variant` 分流，不再由独立管理卡重复承载导航。admin active 普通组合只保留「调仓」outline +「申购赎回」primary，「份额变动事件 / 快照管理 / 现金流水」收进「更多」；draft/closed 保留激活/重新激活与「更多」，菜单另含申赎、调仓记录。viewer 普通详情只有只读「更多」，不显示快照、激活、调仓、删除；台账不接收净值管理菜单，投资人 Tab 不渲染组合操作组。持仓管理入口从本详情导航撤下，既有持仓展示、产品/平台详情导航与管理路由保留。桌面为 Dropdown（悬停打开，指针可移入，悬停模式离开后关闭；点击固定，键盘打开/选择，Escape 关闭并归还焦点；键盘焦点在菜单内时不被离开定时器关闭），移动为底部 Sheet（44px 行高）。普通组合删除归「危险操作」分区，不与主操作并排；桌面台账保留直接激活/删除形态，移动台账 draft/closed 保留激活与「更多」Sheet（删除归危险分区）。
- **组合详情 IA 与只读口径卡（#944 Phase 7.2）**：净值型组合详情桌面顺序为「统计 → 资产与净值 → 持仓 → 绩效 → 投资人/交易入口 → 组合口径」，`lg` 起资产与净值双栏、窄屏单列，不再使用固定 360px 右栏。移动顺序为「统计 → 操作 → 资产 → 净值 → 持仓 → 绩效 → 组合口径」，不另加桌面入口组；草稿不渲染空的市场/绩效段，桌面仍保留投资人入口、交易记录仅活跃态显示，台账继续使用独立 `LedgerContent`。`PortfolioInfoCard` 双端共享，只展示成立日期、类型、状态与代码；成立日期取 `started_at`，缺失显示 `--`，不把创建时间或快照时间冒充成立日，也不在页头重复。口径卡无编辑或管理入口；桌面投资人/交易入口复用 `?tab=investors` 与 `/trades` 既有路由。
- **说明触发器单点 = `components/shared/InfoTip.tsx`**（#943 S2）：只收起已批准的解释文案，消费方传相邻标题的可访问名与完整说明；底层复用 `Popover`（含 Dialog portal context、主题与碰撞避让），不在 SVG 上绑 hover、不写原生 title 或手写定位。自动快照开关的说明按钮与 `Label`/`Switch` 同级，不能嵌进 label 点击链。绩效六项指标与概览说明共用同一触发器；持有天数、负现金、快照缺口、净值不一致、短持有期年化与操作错误仍直接可见。
- **产品身份区单点 = `components/shared/ProductIdentity.tsx`**（#935 S2）：三类产品详情共用的非吸顶全文身份区，位于页眉之后、概览/操作与一栏或两栏内容之前，双端用主内容宽度（不塞桌面 360px 右栏）。产品全名 h2 16/600/24 自然换行、不限行、不 ellipsis/clamp/仅 tooltip（长连续串 `break-words` 必要时断行）；代码/市场 14px/22px 在名称下方；平台-产品页的平台名（回退平台码）为独立上下文行；停用 badge 与名称分别占位、窄屏可换行。消费方传入既有格式化结果（显示码/港互认双码/市场格式/空名回退代码/现金 market 口径不变），组件不新增请求、缓存、路由判断或 scroll state。管理操作行由 `ProductManageDetailContent` 在身份区下方维护（编辑/启停图标+文字、删除独立图标危险色，随正文滚动），不扩成通用详情框架。
- **持仓产品组件双形态单点 = `components/shared/HoldingProductCard.tsx`**（#944 Phase 6.2/7.1）：组合「按产品」的移动分支使用无边框分隔线行；桌面组合持仓改由 `PortfolioHoldings` 内通栏账表承载，`HoldingProductCard` 的桌面圆角卡仅继续用于平台详情等既有消费方。字段、显示码、收益格式化与 InfoTip 口径同源；`cardGridClass` 的移动分隔线分支只传给组合产品区，平台详情与在途卡继续原卡片间距。
- **流水卡解剖单点 = `components/shared/FlowCard.tsx`**（#907/#944 Phase 8.1）：`FlowCard` / `FlowCardHeader` / `FlowCardKVGrid` / `FlowCardDetails` / `FlowCardActions` / `FlowCardSubBlock` 六件组合承载流水卡（容器 / 头行 / 常显与核对明细 kv 栅格 / 键盘可访问展开件 / 操作行 / 结对子块），调仓/申赎/事件/快照四页移动分支共用；新流水卡禁止手写卡内 flex/grid 解剖，形态与字段口径详见 §8。桌面表格分支不消费（仍走 `Table`）。
- **历史净值卡单点 = `components/shared/ProductNavCards.tsx`**（#934）：四列共享轨道（`grid-cols-[max-content_repeat(3,minmax(max-content,1fr))]`，日期按完整内容占宽、数值列不小于内容宽），表头/ul/li 逐层 subgrid 继承同一祖先轨道与列间距，**禁止每行独立分列**（#934 叠印根因）；宽度不足时仅数据区横滚，表头与全部行同步横移，标题与查看更多留在滚动区外（tabIndex/role/aria 提供键盘滚动入口）。三个详情页（产品/平台-产品/平台管理-产品）桌面净值分支按**内容容器宽**降栏：外层套 `@container` **父**元素（容器查询按最近祖先容器求值，挂在栅格自身不生效，同 #774 口径），容器 ≥800px 双栏（右栏 360px、栏距 16px）、<800px 单栏左组上/右组下；现金/虚拟产品分支不参与。800px 为 #934 SPEC v2 定稿阈值（决策与实测记录见该 issue 正文）；守卫 = 三个 detail spec 的「A2 桌面父布局：容器 799/800 降栏与顶部导航几何」用例 + `e2e/helpers.ts::expectNavHistoryGeometry` / `expectNavDetailColumns` / `expectTopNavDetailLayout` 几何断言。新增消费方或触及该布局时按本条复核，不得绕开共享轨道另写分列。
- **净值区间组按内容宽换行（#941）**：`PortfolioNavTrendCard` 与 `ProductNavCards` 的两套区间容器都用 `min-w-0 max-w-full flex-wrap`，受所在卡片内容宽约束；单颗按钮 `shrink-0 whitespace-nowrap`，窄屏/放大时整颗换到下一行，不拆字、不省略、不缩字号、不做横向滚动。常规宽度四颗同排，间距与横向 padding 只能沿用既有档位；两套原有文案、字号、默认项、选中态与 `aria-pressed` 不统一成同一视觉样式。按钮键盘焦点环必须完整可见，因此不得用 `overflow-hidden` 裁切换取“无溢出”。#934 的历史数据局部横滚是另一职责，不得借此把区间组改成滚动容器。
- 双端页面优先 `components/shared/` + `variant`，只有布局本质差异才拆 `components/mobile/` / `components/desktop/`。
- 弹窗一律走 `Dialog` / `AlertDialog` 基件；提示一律走 `Alert` / toast，不自绘浮层。
- **待建基件登记**（规格已定、随首个使用页面落地；未建前禁止手写同功能件）：`checkbox`（#146 首个使用，shadcn 基件，`rounded-sm`、选中态 `bg-primary`）、`date-range-picker`（#125，形态见 §10）、`pagination`（#125/#126，形态见 §11）。
- **`sheet` 底部抽屉基件已建（#904 P1）**：`components/ui/sheet.tsx`（Radix Dialog 底部形态：handle + 标题行 + 内滚 body + sticky footer）；行高 44px 由消费方保证（触控目标）；首消费 = ProductsContent 移动 ⋯ 抽屉（平台管理/资产分类管理/批量净值同步）与移动筛选 sheet（P1b 同批落地）。无入场动效（仓库未装 tailwindcss-animate，§14 动效词汇）。**已知限制（#917 L2 登记）**：sheet 尚未带 dialog.tsx 的 #191 `DialogPortalContainerContext` 机制——现消费方（链接/按钮/Select，Select 浮层自带 disableOutsidePointerEvents）不命中；**迁带 date-range-picker（Popover）的筛选面板进 sheet 前必须先补同款 context** 并加「sheet 内日期 picker 可点选且不误关 sheet」用例（datepicker-in-dialog 手法），否则 #191 复现。

## 14. 交互反馈模式

- **加载态二选一**：区块级 = 居中 spinner（`Loader2 animate-spin` + muted 文案，用于表格/卡片/整页加载）；按钮内 = `Loader2` 替换前缀图标 + `disabled`（用于提交/确认/同步等动作）。**现状以按钮内为主**，新代码动作类加载一律按钮内，区块级仅限首次数据加载。
- **查询失败态 ≠ 空态**（#647/#655 定形、#663 收敛）：读侧查询失败一律渲染共享 `components/shared/QueryErrorState.tsx`（「加载失败：{后端 `detail.message`}」+「重试」链接按钮），**不得**用 `EmptyState` 或裸 `<p>` 承载，更不得让失败落进空态文案——「取不到」被说成「没有数据」会把后端故障读成业务事实（净值走势卡曾如此）。**本条「一律」只管页面与区块级**：Dialog 内的读侧失败走 destructive inline Alert +「重试」（`CashMarketValueUpdateDialog` 的覆盖记录块），按本节「错误提示分工」属阻断性提示，同样透出后端 `detail.message`——是既存分工而非漏改，不要为「统一」把它换成 `QueryErrorState` 而丢掉 destructive 语义。形态选择：卡内传紧凑 `className`（现口径 `mt-3`），页级/区块级用组件缺省 `py-8 text-center`（与 `EmptyState` 同尺寸，不新造视觉语言）。§8 的三种空态变体不含失败态——失败不是「空」的一种。
- **说明浮层 InfoTip**（#943 S2）：真实 `button` 承载 lucide Info，提供可访问名、`aria-expanded`/`aria-controls` 与打开时 `aria-describedby`，键盘焦点有可见 ring；触控盒至少 44×44 CSS px，图标仍用 14px 紧凑档，触发器本身预留 44×44 布局空间，透明放大区域不得覆盖相邻数值。桌面鼠标悬停或键盘聚焦打开，指针可移入文字继续阅读，离开触发器和浮层后关闭；移动/触控点按打开，再次点触发器或点外部关闭，点文字本身不误关；Escape 关闭。浮层最大宽 320px 且不超过 `100vw - 2rem`，在 320/390px 与桌面 360px 窄栏内完整换行、不裁切、不新增页面级滚动容器。组合详情的资产与净值在 `<lg` 时单列、`lg` 起双栏，说明触点在窄栏与单列中均完整换行、不裁切；持仓桌面通栏账表与移动分隔线行按 §8/§13 的端侧形态适配，320px 下不得撑宽页面或遮挡说明触点。
- **首页汇总失败与首载**（#944 Phase 3.2）：summary 首次加载失败时内容区进入共享 `QueryErrorState`，移动端标题与账户区保留；申赎/调仓列表首次加载失败时保留已成功的汇总，只在活动区块显示 `QueryErrorState`，不得落成「暂无活跃组合」或「暂无交易记录」。首载数据未落定前只显示 `LoadingState`，不提前渲染 0 总额/0 待办。汇总后台重取失败时保留已有汇总，并在汇总区显示「汇总更新失败，当前显示上次数据」、错误原因与 summary 专属重试，不伪装成活动失败或空态；活动列表后台重取失败时保留已有列表、显示失败块和活动专属重试，汇总容器以 `aria-busy` 标记在途状态。两类重试互不触发对方查询。
- **局部加载态**（筛选变更/翻页等局部刷新，#125/#126 起；#944 Phase 7.3 修订）：保留旧数据，以区块右上角 `Loader2` 小 spinner 标记更新，不再把整块数据压暗；不切换为空态或区块级 spinner，避免闪烁与布局跳动。首次进入页面的全量加载仍走区块级 spinner（§8）。**卡片数据区的重取同理（#683 D-1）**：同一区块右上角显示 `Loader2`，并把该状态显式写成 `aria-busy="true"/"false"`——定位器契约禁止按 Tailwind 工具类定位，故这个属性同时是状态语义与 E2E 锚点（手法同 #650 的 `aria-current`）。这里**不得**为消除「旧值看起来像当前值」的观感而删掉 `placeholderData: keepPreviousData`（那是筛选/翻页不闪烁的既有行为，属另一项业务判定）；也不得只看数值是否变旧来判——`isRefetching` 为真即标 busy，首屏在途不标。
- **操作区按钮层级**：每卡片/区块主操作唯一（`default` primary solid），其余 `outline`/`ghost`；危险操作与主操作分区放置，不与主操作并排。
- **移动组合详情页内操作区与长页回顶（#937）**：操作区取消 fixed/sticky 底栏，回到正文流；净值型位于统计之后、资产分布之前，draft/台账沿用各自既有位置。保留「调仓 / 申购赎回 / 更多」单组三列与主次样式，移动操作按钮统一 44px 高、更多 44×44；不增加卡片、重复操作条或 `pb-24` 专用预留，正文底部保留回顶按钮尺寸与安全区所需的普通留白。仅移动组合详情在 `scrollY >= 3 * innerHeight` 时显示右下角「回到顶部」44×44 按钮，挂正文间距容器外，右/底距为 `1rem + env(safe-area-inset-*)`；短页不显示，scroll/resize 重算，层级为 §7 的 `z-40`。点击或键盘触发瞬时回到文档顶，不改 URL/视图参数、不发业务请求、不加平滑滚动；页头恢复展开，按钮隐藏时若持有焦点则交还页头返回出口。该按钮是阅读导航，普通详情、投资人和台账长页均可使用，不新增管理权限。
- **视图状态即时切换（#915 B2）**：持仓明细「按产品」视图各大类分区头下的子集切换 chip 行（`PortfolioHoldings.tsx::SubDimSwitchRow`，双端同形态、`flex-wrap` 换行）——「默认」+ 该大类非禁止维，chip 形态沿用净值区间先例（`rounded-full px-3 py-1.5 text-xs`，激活 `bg-primary` 白字、未激活 `bg-muted`，`aria-pressed` 表态）；点击即时重分组、无 toast/无加载态/无确认（纯视图状态，URL `?sub=` 持久化、不写回配置，口径正文见[业务规则·组合](../reference/business-constraints.md#rule-portfolio) display_config 条）。现金（0 维）与「其他」桶不渲染切换行；按平台视图不渲染。E2E 门禁：`frontend/e2e/portfolio-subset-switch.spec.ts`（双端）。
- **错误提示分工**：操作结果（增删改/同步/确认的成败）用 **toast**（success/error/warning/info 四型，映射 success/destructive/warning/success 色系，info 不单独设色）；表单校验、数据完整性、阻断性提示用 **inline Alert**（`default` / `destructive` 两 variant；校验通过/警告用 success/warning 的 `bg-*-soft` + `border-*/30` 组合，不新增 Alert variant）。
- **后台任务进度**（sync-job 类，#146 起）：进行中 = 状态 Badge（`running`→`warning`，§1.3）+ `Loader2` + 文案的行内区块；不引入 Progress 条组件（任务无可靠百分比，YAGNI）。终态反馈仍走上述 toast 分工，页面数据随之刷新。
- **危险操作**（删除、关闭组合、强制操作）统一 `AlertDialog` 二次确认，确认按钮 solid `bg-destructive-solid text-white`；普通确认走 `default`（primary 靛蓝）。
- **破坏性操作两段式确认**（dry_run → confirm，#146 起）：第一步预览 AlertDialog 列出影响范围，文案模板「将删除 X 张快照（YYYY-MM-DD ~ YYYY-MM-DD），此操作不可恢复」；确认按钮沿用 solid `bg-destructive-solid text-white`；拿不到预览结果时禁止直接执行。
- **动效（现状登记，2026-08-29；克制原则）**：全站仅三类动效——① 加载 spinner（`Loader2 animate-spin`）；② toast 滑入（globals.css `slideIn` keyframes，唯一消费点 ToastContainer）；③ 基件内置过渡（tailwindcss-animate `animate-in/out`、hover/状态切换 `transition-colors`）。不新造关键帧、不引入滚动/视差动效——金融界面以静为默认。

## 15. 图表表达规范

- **资产构成卡默认条形图，饼图为可选视图**（#944 Phase 5.1）：切换仅用组件本地状态，不写 URL；两种视图经 `sortAllocationItems` 同一路径按市值降序，身份色不随排名重排；合并后的「其他」也参与降序，不固定垫底。**展示总数最多 6 类**：超过时保留最大的 5 个具名类，尾部与既有「其他」合并；金额与占比必须守恒。资产合并项用 `otherColor(theme)`（light 为 C8 深灰蓝，与现金 C7 区分）；无「现金」语义的普通多序列仍用 `CHART_OTHER`（C7 灰蓝）。
- **条形行**：分类名、零基 0–100% 共享刻度轨道、占比（1 位小数，§4）与金额同排；行可聚焦并有可见 ring，hover/focus 显示金额 tooltip。焦点样式只限条形行，不得扩散到饼图或其他 SVG。
- **图例位置**：环形图左图右例（移动端上下堆叠），图例 = 色点 + 名称 + 占比（1 位小数，§4）；不单独发明图例样式。
- **tooltip**：金额 `${formatCurrency(value)}`（可带「元」补语义）、净值 `formatNav`、占比 1 位小数；tooltip 内涨跌数值用 `text-gain`/`text-loss`，图表元素本身（线/柱/扇区）永不用涨跌色。
- **折线**：净值主线恒 `NAV_LINE`（C1 靛蓝）、无数据点圆点（`dot={false}`）、参考线用 `CHART_OTHER` 灰蓝虚线；多序列按 C1→C8 顺序取色。
- **净值图绘图区（#941）**：`NavCurve` 的 `margin.left=0`，Y 轴 `width="auto"` 由真实 `formatNav` 刻度决定宽度，右边距从 `8px` 起步；不得恢复库默认 60px 轴宽再与固定左边距叠加。X 轴首末日期分别向内锚定（首 `start`、末 `end`），完整保留 `MM-DD` 语义且不被有限右边距裁切；参考线「初始净值」用 `insideRight` 放在图内靠右，保留原参考值与显示条件，不压首末刻度。普通六字符刻度（如 `0.9000–1.0200`）在图表宽 ≥230px 时左预留 ≤60px、右预留 ≤12px；长刻度必须自动扩宽，不能把普通阈值当截断上限。`formatNav` 四位小数、自动 Y 域、数据顺序、插值、主题色与桌面 300/移动 200（产品 220）图高均不随布局调整改变。

## 16. 图标体系与着色

- **图标库**：全站统一 `lucide-react`（49 处消费，唯一图标来源）；不引入第二图标库、不内联自绘 SVG 图标。**刻意例外**：品牌标识 `BrandLogo`（环 + 突破箭头）是品牌资产而非 UI 图标，内联 SVG 且随主题 token 取色（`hsl(var(--primary))` / `hsl(var(--foreground))`），规格见 §20.5；不得据此把普通 UI 图标也改成自绘 SVG。
- **尺寸档（现状登记，2026-08-29）**：默认 `h-4 w-4`（16px，与 14px 正文配套）；移动端导航/大触面 `h-5 w-5`（20px）；紧凑徽章内 `h-3.5 w-3.5`（14px）。三档之外不新造。
- 图标默认继承文字色（不加颜色类）；需要语义时与同行文字共用同一 token：成功 `text-success`、警告 `text-warning`、失败 `text-destructive`、涨跌图标随数值走 `getReturnColorClass`。
- 状态指示小圆点（通知级别、交易日标记、方向标识）用对应 token 的 solid 色（`bg-success` / `bg-warning` / `bg-destructive`）或 `lib/colors.ts` 分类色，不用 `-soft`。

## 17. Dark mode 启用条件（#872 已启用：PR1 闭合前置、PR2 启用）

四项前置条件已于 #872 PR1 闭合、PR2 完成启用；**暗色已生效**（默认 system，D2）。历史中间态（PR1 合入至 PR2 合入之间「⓪①② 已闭合、启用待 PR2」）措辞已归档于 issue #872 评估结论；现态：本节 = 「已闭合 + 已启用」，§1.1 第 5 条 = 「已启用」。

- ⓪ **双通道对齐（已闭合）**：`.dark` 全量按 §1.2/§1.4 登记 hex 以 HSL 2 位小数精确往返重写（含语义色 ±1–2/255 舍入漂移修正，17 对照项中 12 项）；light `:root` 既有 33 个色彩 token 零改动（新增 destructive-solid 属 D3 批准的新增，非修改）。
- ① **对比度实测（已闭合，数字如下）**：WCAG 2.x 相对亮度公式计算，可复现脚本见 issue #872 评估结论（SPEC 附录 A）。文本阈值 4.5:1，对暗卡 `#171E2C`（注明者除外）：foreground 14.22 / foreground(页面底) 15.68 / foreground-secondary 7.67 / muted-foreground(卡) 5.49 / muted-foreground(页面底) 6.05 / muted-foreground(muted 底) **4.65（最劣，禁止再深）** / primary 6.67 / gain 5.82 / gain(页面底) 6.42 / loss 6.72 / warning 8.21 / destructive 5.60 / success 6.67；soft 底上 foreground：gain 7.51 / loss 7.50 / success 7.30 / warning 8.02 / destructive 7.00；primary-foreground/primary 7.14；**白字/destructive-solid(dark) 5.50**——原白字/destructive `#EE6B80` 仅 2.98 不达标，故新增 destructive-solid 载体（D3），light 渲染逐字节不变（白字/实际 light 渲染 `#C42746` = 5.64）。非文本阈值 3:1（R1 口径 = 识别组件/状态所必需元素）：ring/卡 6.67、ring/页面底 7.36、input/卡 3.26、input/页面底 3.60、destructive-solid/卡 3.03；装饰性 border/卡 1.34（light 1.24 同态，不设硬线）。图表暗色板 8 色对暗卡 ≥3:1（§2 表）。
- ② **存量清理（已闭合）**：18 处硬编码中性类名逐条处置——7 处替换语义 token（登录页 4 + 饼图图例 1 + 原生 checkbox 边界 2；后者经 chromium/webkit 双引擎像素实测零渲染变化，Tailwind v4 preflight `border: 0 solid` 且无 border-width utility）；9 处 `text-white` 保留、载体换 `bg-destructive-solid`（白字 5.50:1）；2 处 `bg-black/80` 遮罩保留（scrim 职责即压暗底层，非信息载体）。任意值色 `[#xxx]` 全仓为零（grep 核实）。逐条清单见 issue #872 评估结论（SPEC §7.3），grep 复核与清单一致。
- ③ **图表暗色评审（已闭合）**：结论 = 独立提亮暗色板（§2），红涨绿跌与 NAV_LINE 约束在暗色下仍成立；recharts 默认色治理结论见 §2（PR2 落地）。

门禁顺序（已执行完毕）：PR1 未引入 `dark` 类/`dark:` 变体；PR2 在四项闭合证据在案后引入 ThemeProvider（基件 `alert.tsx` 自带 `dark:border-destructive` 为既存例外，保留）。

- **首绘取色时机（已验证 2026-11-13，撤销原误判限制）**：图表组件（含 `AssetAllocationPie` 图例，它在 `ResponsiveContainer` 之外）与列表/卡片的内联 style 圆点同经实测（domcontentloaded 时刻取样 + 原始 SSR HTML，chromium 与 webkit 双端），均不在 SSR 首绘产物中；圆点于客户端取数完成后首次出现即为暗色板。受影响清单按实测重写：trades 方向 dot、cash-flows 现金 dot 与在途卡（`HoldingInTransitCard`，实际渲染于 cash-flows 页）、portfolio 饼图图例；positions 路由在本种子下不渲染内联圆点，该项不作为证据（在途卡组件经 cash-flows 页 exercised），不再沿用未经确认的「8 组件约 9 处」计数。证据归档：群共享 `shared/pr876-evidence/`。

## 18. 存量债与迁移策略

- 本规范落地时已完成：语义 token、Badge variant、盈亏色函数、图表色板、全部 `(text|bg|border)-调色板-数字` 类名清零（#127）；中性色目标值一次性切换（2026-08-29，§1.4）。
- 已登记、渐进收敛的存量：`text-[Npx]` 任意值（1 处 / 1 文件，ESLint ratchet 豁免见 §1.5）、w·h 系一次性尺寸任意值（如 `h-[60vh]`、`sm:max-w-[500px]`，未拦截、不强制清理）、原生 `<input type="checkbox">` 手写件（#146 收敛为 §13 登记的 checkbox 基件；#872 PR1 仅替换其 border 色为 token，基件收敛仍按顺手原则）。原则：**改动到该页面时顺手替换，不单独开重构 issue**。`text-xl` 数值中间档自 #935 起登记为数值专属角色（§5），不再计作标题存量债。
- **全站普通标题字号已一次性迁移（#935，2026-11-20）**：页面标题 24→20px、普通分区标题 18→16px，规范与实现同批交付（本 §5/§13/§19 与代码同一批次）。覆盖 `PageHeader` h1（展开/折叠统一 20/28）、`CardTitle` 默认值（24→16/24）及消费者显式 `text-lg` 覆盖、`DialogTitle`/`AlertDialogTitle`/`SheetTitle`（18→16/24）、非 PageHeader 页标题（双端 dashboard/设置首页/移动设置子页）、自写 h3 分区标题（交易记录/持仓明细/净值三卡/平台分布）；面包屑导航 12→14px/22px。数值/品牌专属角色（§5 登记）保留不动；登录品牌与 14px 紧凑提示卡标题显式补 `leading-none` 保持原形态。三类产品详情同批改为短页标题「产品详情」+ `ProductIdentity` 正文全文身份区（§13）。#386/#394 的 24px 收敛史保留于下条，不撤销其当时决策。
- **中性色硬编码已清零（#872 PR1）**：18 处处置完毕（7 替换 / 9 白字载体换 destructive-solid / 2 遮罩保留），`slate/gray` 类名不再属渐进存量。**刻意例外**：`text-white`（solid 危险载体与未读气泡，双主题白字）与 `bg-black/80`（Dialog/AlertDialog scrim，双主题同值）——回流判定以 issue #872 处置清单为准，不得一律当违规清理。中性色 ESLint ratchet 已于 #872 PR2 落地（§1.5 第 3 道）。
- **登录页渐变端点色已 token 化（#872 PR2）**：`from-blue-50 to-indigo-100` → `from-success-soft to-secondary`（light 浅靛→浅灰蓝，dark 自动随 token），消除暗色整页浅底残留（C1）。渐变端点色 from-/to- 的 ESLint 拦截不在 D6 已批范围，未加（扩面须维护者另行批准）。
- **移动账户卡随顶栏迁移退役（#944 PR4.2）**：删除 `app/m/dashboard` 的账户卡、账户区标题与专用 `p-2` 承载类，同时移除不再使用的 `AccountMenu` 导入；账户操作统一由 `MobileLayout` 顶栏承载。`Card` / `CardContent` 仍有其他消费者，不做全局删除；顶栏复用既有共享组件与 `h-14` / `px-4` 档位，不新增导航状态或端侧专属尺寸。
- **危险载体/前景配对缺陷家族已归零（#874）**：PR1 迁移 9 处 `bg-destructive text-white` 载体后，#874 再迁移 4 处 `bg-destructive text-destructive-foreground`（PortfolioListContent/TradesContent/SubscriptionsContent/DeletePortfolioDialog；light 1.59:1、dark 预案 1.51:1——前景 token 本为 soft 底定义，压 solid 底属配对错误）至同一分工，白字 light 5.64:1 / dark 5.50:1。`--destructive-foreground` 仅用于 soft 底（badge/Alert/错误块）；全仓剩余 `bg-destructive` 仅 NotificationBell 状态圆点（非文字载体）。
- 已登记、**不适用**「顺手替换」原则的存量：~~桌面页面标题 `<h1>` `text-3xl font-bold tracking-tight`（12 处 / 12 文件）~~ **已于 #386 一次性整体收敛为 §5 页面标题档**（`text-2xl font-semibold`，含 4 个共享组件移动分支的 `font-bold` 字重同步对齐，2026-09-05）。~~收敛时核对移动端发现新漂移：3 处移动端专属薄壳页 h1 不符 §5~~ **已于 #394 判定为非刻意紧凑形态并一次性对齐 §5 页面标题档**（`app/m/dashboard` 字重 `font-bold`→`font-semibold`；`app/m/portfolio/[code]` 与 `positions` `text-xl font-bold`→`text-2xl font-semibold`。判定依据：#386 已将共享组件移动分支收敛为 semibold，此三处为全站仅剩孤例，且 §6 不发明端侧独有字号，2026-09-06）。

---

## 19. 新页面 / 大改自检清单

提交前对照（全部「是」方可合入）：

1. 颜色只出现语义 token 与 `lib/colors.ts` 导出，无调色板类名、无组件内 hex 字面量（ESLint 已过）。
2. 涨跌数值色只来自 `getReturnColorClass` / `getReturnBgClass`；badge 状态色经 `getStatusBadgeVariant()`，未占用涨跌色（§1.1/§1.3）。
3. 数字全部经 `lib/utils.ts` 格式化函数；表格数值列右对齐 + `number-cell`，统计卡主数值与标签/注记同侧左对齐（§5）；占比走 `largestRemainderPercents`，无散装 `toFixed`（§3/§4/§8）。
4. 文字字号走四档（20/16/14/12，§5）；`text-lg`/`text-2xl` 只出现在 §5 登记的数值/品牌专属角色；无 `text-[Npx]`、`p-[Npx]`、`gap-[Npx]`、`rounded-[Npx]` 任意值（§5/§7）。
5. 卡片无投影（浮层除外）；圆角走 `--radius` 派生档；间距走既有档位（桌面 `p-6`/`space-y-6`、移动 `p-3`/`space-y-4`）（§7）。
6. 表格：表头 muted 常规字重、无底色无斑马纹；空态 / 首次加载 / 局部刷新加载按 §8/§14 既有形态，不自造。
7. 筛选栏 / 日期区间 / 分页如使用，形态与控件排序符合 §9–§11（控件 `h-9`、时间→状态→实体→类型、即时生效、无结果空态带重置入口）。
8. 状态徽标一律 `Badge` + variant；无手写 `bg-*-100 text-*-800` 件；弹窗走 `Dialog` / `AlertDialog`，提示走 toast / `Alert`（§13/§14）。
9. 图表：色板按序消费、净值主线 `NAV_LINE` 不用红绿、超 6 类合并「其他」（场景用色见 §15）、图表元素本身不着涨跌色（§2/§15）。
10. 双端同语义：共享组件 + `variant` 适配，未发明端侧独有色彩或字号，信息列的增删双端一致（§6）。
11. 暗色（#872）：无硬编码中性色与渐变端点色；图表取色走 `lib/colors.ts` theme 访问器；浮层表面：Dialog/AlertDialog 走 `bg-background`（与 light 侧同 token parity）、Popover/Tooltip 走 popover/border token；主题偏好仅经 next-themes（storageKey=theme），不进 zustand、不与 #68 水合门耦合。新增内联 style 取色元素须说明其出现时机（客户端取数后）；首绘无内联取色圆点已验证（§1.1 第 5 条/§17）。

---

## 20. 导航壳 / 品牌标识 / 设置页结构（#878）

> 本节记录 #878 导航与设置重构的**结构决策与理由**（尺寸/状态等读代码可得的事实以组件为准，这里只钉跨文件约定与取舍）。承继 #650 导航单源。

### 20.1 导航单源与语境分布

- **唯一来源仍是 `components/shared/navItems.ts`**（#650）。`NavItem` 在 #878 增加 `inNav`（是否进桌面顶部主导航，#944 起消费方为 `TopNav`）；`mobileTab` 沿用 #650 语义；`mobileEntryHost` 自 #894 改名 `entryHost`（语义已是端无关的「语境入口宿主页」）。派生函数：`sidebarItemsForRole`（角色过滤后再剔 `inNav:false`，函数名保留）、`mobileTabPaths`（移动底部 Tab）、`mobileExitPaths`（**双端**站内出口；函数名属历史遗留——E2E compare 的 baseline 用 base 的 e2e 配 head 的 src 且 import 本函数，改名会弄红 baseline capture 且无豁免，故改名留待合入后 follow-up）。**新增导航项必须同时决定 `inNav` / `mobileTab`，填 `false` 就得补页内入口 + 返回宿主**，否则「可达即被困」（#650 验收第 4 条）。
- **站内出口是页头职责、单点组件、双端共用一份派生**（#904 取代 #894 的布局层渲染）：`MainLayout` 与 `MobileLayout` **不再渲染返回行**；管理页与钻取页统一消费 `components/shared/PageHeader.tsx` 的面包屑槽（crumbs），出口 href 仍由 `navItems.ts::mobileExitPaths` 派生（单源不变），首 crumb 承载返回图标 + `aria-label`「返回{宿主/祖先名}」（保留 #894 既有 E2E 可及名）。**判据按端取轴**：桌面看 `inNav`（顶部导航）、移动看 `mobileTab`（底部 Tab）——两个独立的轴，当前 `inNav:false` 与 `mobileTab:false` 的集合恰好相同，沿用单轴会在出现 `inNav:false, mobileTab:true` 的项时静默漏掉桌面出口。**桌面窄屏（`<lg`）是最严重的一档**：TopNav 不显示、底部导航按 `mobileTab` 过滤同样不含该页，紧凑头栏 Logo 指向 `/dashboard` 而非宿主页，故出口在该档是页内唯一出路。#894 教训保留：**导航口径变更必须同批复核相关 skip 前提**，否则覆盖无声蒸发。
- **页面级导航控件必须有可访问名**（#894，#904 扩至面包屑）：面包屑首 crumb 与任何图标-only 返回控件必须带 `aria-label` 且名字点明返回目标（如「返回组合列表」/「返回组合详情」/「返回产品管理」），使读屏可辨、E2E 可按 `role+name` 唯一定位；面包屑祖先链接承载跳级返回，可见文本为祖先名、aria 为返回语义。与 §8 操作列控件的 `title`/`aria-label` 纪律同源，但适用对象是页面级导航控件、不是列表行操作。
- **组合子页面包屑显示真实组合名**（#943 S1）：申赎、调仓、份额事件、快照、现金流水、组合内产品/平台详情与持仓管理统一复用 `usePortfolio(code)` 缓存；一级子页为「组合 / {portfolio.name}」，快照补「组合」列表层，深层页保留下游产品/平台层。`href` 与路由仍用组合 code，原有代码元信息不删；组合查询加载/失败分别进入既有 `LoadingState`/`QueryErrorState`，不得用 code 冒充成功名称或落成空业务态。长名允许换行，CJK 与无空格 Latin 连续串都不得撑破窄栏；完整可见文本与 `返回组合列表`/`返回组合详情` 语义不变。
- **管理入口走语境分布，不堆进设置页**（#878 拍板，入口式不删页）：投资人管理入口在**组合页头部**（admin-only，`PortfolioListContent`），平台管理 + 资产分类管理入口在**产品页头部**（双端，`ProductsContent`，`${basePath}/platforms`、`${basePath}/asset-classifications`）。三项 `inNav:false` / `mobileTab:false`，路由本身保留；`entryHost` 分别为 `/portfolio`、`/products`、`/products`。理由：管理动作发生在其宿主语境（看组合时管投资人、看产品时管平台/分类），比收进设置页更近；移动端底部 Tab 位宽有限（#650 口径），同样走页内入口。
- 设置页**不再承载**平台/投资人管理入口（原移动设置页的平台管理块已移除）；设置 = 纯系统设置（§20.4）。

### 20.2 桌面顶部导航与窄屏回退

- **宽屏（`≥lg`）使用横向 `TopNav`**（#944 V2-2）：品牌在左、主导航居中/靠左、`NotificationBell` + `AccountMenu` 在右。TopNav **静态、非 sticky**，不与 `PageHeader` 的 `top-0 z-30` 吸顶层争位；导航壳高度沿用紧凑顶栏的 `h-14` 节奏。
- **侧栏与收起机制退役**：删除 `components/layout/Sidebar.tsx` 及 `uiStore` 的 `sidebarOpen` / `sidebarCollapsed` 与切换方法；不再有侧栏宽度、折叠偏好或 Tooltip-only 标签。`main` 的 `min-w-0` 与 padding 保持不变，TopNav 不占主区横向宽度。
- 主导航继续单源消费 `navItems.ts::sidebarItemsForRole`（函数名保留），激活判据为一级路径精确匹配或前缀匹配，`aria-current="page"` 与视觉激活同源；链接保留 `aria-label={item.label}`，不依赖图标或 Tooltip 传达名称。
- **窄屏（`<lg`）回退**：TopNav 隐藏，`MainLayout` 保留 `h-14` 紧凑顶栏（Logo + 通知 + 账户）与 `BottomNav`。任意宽度下「用户菜单」触发器恰好一个可见；1024px 顶部导航几何与 900px 紧凑顶栏/底部导航回退由 `e2e/top-nav.spec.ts` 守门。

### 20.3 账户区位置

- **桌面账户区在 TopNav 右侧**：`NotificationBell` 在左、`AccountMenu` 在右，与紧凑顶栏保持同一顺序；`AccountMenu` 使用紧凑头像触发器，下拉内展示姓名/角色与修改密码/退出登录。紧凑触发器避免长姓名在 1024px 挤压主导航，账户身份与操作不减少。
- **桌面窄屏账户区在紧凑顶栏右侧**，与宽屏共用同一组件。
- **移动站所有 `/m` 内容页由 `MobileLayout` 顶栏承载账户与通知**（#944 PR4.2）：左侧 `BrandLogo` 返回 `/m/dashboard`，右侧 `NotificationBell` + 紧凑 `AccountMenu`；顶栏静态、不 sticky，避免与 `PageHeader` 的 `sticky top-0 z-30` 争位。`/m/login` 不套该布局。移动首页底部「账户」卡退役，修改密码 / 退出登录对全角色始终可达，不再依赖首页滚动位置。
- **移动底部避让**：底部导航继续由 `mobileTabPaths("/m")` 单源判定，仅一级 Tab 页显示；`main` 保留 `pb-16`，布局按显示条件叠加既有 `safe-area-pb`。`BottomNav` 保留 `safe-area-pb`、内容行 `h-16` 与 `z-50`，末操作滚到底时不得被固定底栏遮盖。
- `AccountMenu` / `ChangePasswordDialog` / `BrandLogo` 放 `components/shared/`，双端共用同一份；`NotificationBell` 同样直接复用，不在移动布局复制通知状态。

### 20.4 设置页结构（桌面三 Tab / 移动分组列表）

- **设置整体 admin-only**（#878 验收：viewer「设置不可达 = 导航无项 + 深链 AdminGuard 拦截」）：桌面 `app/settings/page.tsx` 与移动 `app/m/settings/page.tsx` 页壳均包 `AdminGuard`（viewer 呈「无权限访问」EmptyState）；移动钻取子页对 viewer **整体**回退（`forbidden = !meta || !isAdmin`，不只 logs），弹回设置首页后由首页 AdminGuard 呈无权限态。全角色改密不依赖设置可达，由设置外的 `AccountMenu`（含移动顶栏）提供。
- **桌面**（`SettingsContent.tsx`，仅桌面）：三 Tab——**通用**（合并 外观与主题 / 账户安全 / 数据源 / 系统信息 四卡）、**交易日历同步**、**日志管理**（admin-only，水合前按非 admin 不渲染，承继 #715）。分区卡片在 `settingsSections.tsx`（`AppearanceCard` / `PasswordCard` / `DataSourceCard` / `SystemInfoCard` / `CalendarSyncCard`）。`?tab=` 深链选中分区（calendar/logs，其余落通用），`useSearchParams` 包 Suspense。
- **移动**（`app/m/settings`）：分组列表 `MobileSettingsList`——**通用**组（外观与主题 / 数据源 / 修改密码 / 系统信息）+ **系统维护**组（交易日历同步 / 日志管理）。行级钻取到 `app/m/settings/[section]`（appearance / datasource / system / calendar / logs），子页自带「返回设置」链接、**不渲染底部 Tab**（`MobileLayout` 只在一级 Tab 页显示底部导航，子页既非一级 Tab 也非 `mobileExitPaths` 出口）。非法/越权 section `router.replace("/m/settings")`。
- **跨 UA 设置子页映射**（#878 L2 Blocker 2）：桌面设置是单页三 Tab、无 `/settings/[section]` 同形路由，`proxy.ts` 对桌面 UA 的 `/m/settings/[section]` 显式映射到 `/settings?tab=<对应分区>`（appearance/datasource/system→general、calendar→calendar、logs→logs，未知落 general），不机械去 `/m` 落 404。
- **修改密码 = 弹窗，双入口**（`ChangePasswordDialog`）：用户菜单（全角色）+ 设置通用（桌面 `PasswordCard` / 移动列表行）。新密码长度规则 **≥6 位**（前端既有校验；后端 `schemas/auth.py` 无长度约束，issue #878 验收原写 ≥8 已回填订正为 ≥6）。弹窗触发器与提交按钮同名「修改密码」，模态 aria-hide 背景下 `getByRole` 同一时刻只解析到一个，E2E 顺序同名点击成立。**关闭收口**：取消/Esc/遮罩/右上角关闭统一经 `requestClose` 清空表单；`isPending` 时禁止关闭（mutation 不可中止，避免「取消」被误解为撤销已提交改密）。
- 主题选择 `data-testid="theme-select"` 保留在 `AppearanceCard`（`e2e/theme.spec.ts` golden 依赖）；light token 逐字节不变（§17）。

### 20.5 品牌标识

- 品牌 Logo 组件 `components/shared/BrandLogo.tsx`：开口环 + 破环箭头（`LogoMark`），环 = `hsl(var(--primary))`、箭头 = `hsl(var(--foreground))`，**双主题随 token 自适应**（token 是裸 HSL 三元组，须 `hsl()` 包裹才是合法颜色，直接 `var(--primary)` 会落回 `stroke:none` 不可见）；`mono` 变体两者皆 `currentColor`。字标 Invest = foreground、Ring = primary。
- 静态资产：`src/app/icon.svg`（渐变 favicon，圆角方底 + 全尺寸 mark，浏览器标签不遮罩、保 16px 辨识）、`src/app/apple-icon.png`、`public/logo.svg` / `logo-mono.svg` / `logo-app.svg`、`public/icon-192.png` / `icon-512.png`（PWA，manifest purpose `any maskable`）。app-icon 渐变 `#3D6FE0→#1E3FA8` + 白色 mark，是品牌固定资产色，**不随主题 token**（与 §16 「图标继承文字色」分工不同：品牌标识非 UI 图标，见 §16 例外）。**maskable/Apple 全幅不透明**：`logo-app.svg` 与由其栅格化的 192/512/apple PNG 为无预圆角的全幅方底（透明角在遮罩/主屏幕会露底或黑角），mark 缩放 0.8 收进中心 40% 半径的 maskable 安全区防裁切；favicon 因用途不同保留圆角与全尺寸 mark。

## 21. 首页信息架构（#944 Phase 3.2）

- 首页顺序固定为：**总资产 / 累计收益 / 合计 XIRR → 待确认事项 → 活跃组合 → 最近交易 → 移动账户**。桌面与移动由 `DashboardSummary` 的 `variant` 共享同一业务数据，链接前缀由 `basePath` 适配。
- 三项核心指标全部来自 `GET /api/dashboard/summary`；累计收益是 active 组合逐项求和后的带符号金额，合计 XIRR 合并 active 组合的外部现金流与各组合期末市值求解，**不平均逐行 XIRR、不用 CAGR 代替**。不可靠原因至少区分无解、现金流不足、持有期不足 90 天。
- 待确认事项保持琥珀提示；顶部笔数取 summary 的待处理计数，明细取申赎/调仓列表并链接到对应组合复核页。活跃组合保留组合/代码、总资产、累计收益与逐行 XIRR；逐行不可靠时显示 `--` 而不把缺失值折成 0，移动端在数值下可见地补充原因，桌面保留原生说明。
- 首页不展示资产构成图。最近交易合并申赎与调仓按业务日期倒序展示；移动端账户卡在本阶段保留，其移除归 Phase 4.2。
