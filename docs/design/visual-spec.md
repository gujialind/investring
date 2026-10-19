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
3. **success ≠ 绿**。绿色被「跌」独占后，完成/确认语义移交**靛蓝 `#2B5CD7`，并兼任品牌主色 primary**（链接、主按钮、focus 环同源）。完成态是系统里出现频率最高的状态，让默认观感就是「一切正常」，减少色彩噪音。
4. **cancelled / closed / draft / 停用 → neutral 灰 badge**。用户主动撤销 ≠ 系统异常，不再用红。
5. **dark mode 暂缓**：token 已写 light + dark 双套预案值（下同），但代码不启用暗色；启用前必须实测对比度（当前 dark 值为同构推算，未实测）。

### 1.2 语义色 token 色值表（配色方案 v1）

> 对比度为对 light 页面底（白卡）的近似值。主色全部落在 5.1–6.0:1 区间（14px 正文数字直用合规）；foreground 对各自 soft 底全部 ≥6.5:1。

#### 涨跌（金融方向色，专属数值场景）

| Token | 用途 | 色名 | Light | Dark 预案 |
|---|---|---|---|---|
| `--color-gain` | 上涨数值、正向收益 | 朱红 | `#CF3526` hsl(5,69%,48%) ≈5.1:1 | `#F0735F` hsl(8,83%,66%) |
| `--color-gain-soft` | 涨相关浅底（高亮行、盈亏卡片底） | 浅朱 | `#FBEBE8` hsl(9,70%,95%) | `#3B2420` |
| `--color-gain-foreground` | gain-soft 上的文字 | 深朱 | `#A02718` hsl(7,74%,36%) | `#F5A89B` |
| `--color-loss` | 下跌数值、负向收益 | 松绿 | `#177245` hsl(150,66%,27%) ≈6.0:1 | `#3FB97F` hsl(151,49%,49%) |
| `--color-loss-soft` | 跌相关浅底 | 浅松 | `#E6F2EB` hsl(125,32%,93%) | `#1E3A2E` |
| `--color-loss-foreground` | loss-soft 上的文字 | 深松 | `#0F5C36` hsl(150,72%,21%) | `#8FD9B4` |

#### 状态色（业务状态语义，禁用于涨跌数值）

| Token | 用途 | 色名 | Light | Dark 预案 |
|---|---|---|---|---|
| `--color-success` | 完成/确认/启用；**兼品牌主色 primary / ring** | 靛蓝 | `#2B5CD7` hsl(223,68%,51%) ≈5.8:1 | `#7FA3F0` |
| `--color-success-soft` | 成功态 badge/提示底 | 浅靛 | `#E8EEFA` hsl(220,64%,95%) | `#22304F` |
| `--color-success-foreground` | success-soft 上文字 | 深靛 | `#1D47B0` hsl(223,72%,40%) | `#A8C2F5` |
| `--color-warning` | 待定/进行中 | 赭珀 | `#96620A` hsl(38,88%,31%) ≈5.2:1 | `#E5AC4A` |
| `--color-warning-soft` | 待定 badge/提示底 | 浅珀 | `#FBF3E2` hsl(41,76%,94%) | `#3D3020` |
| `--color-warning-foreground` | warning-soft 上文字 | 深珀 | `#7C4A03` hsl(35,95%,25%) | `#EBC88A` |
| `--color-destructive` | 异常/失败文字、危险按钮底 | 绛红 | `#C22745` hsl(348,67%,46%) ≈5.7:1 | `#EE6B80` |
| `--color-destructive-soft` | 失败 badge 底、错误提示底 | 浅绛 | `#FAE8EC` hsl(347,64%,95%) | `#42242C` |
| `--color-destructive-foreground` | destructive-soft 上文字（solid 危险按钮上的文字用纯白 `text-white`） | 深绛 | `#8F1A33` hsl(347,69%,33%) | `#F2A3B1` |

**选色逻辑（为什么不是正红/草绿/正黄）**：涨红取朱红（5° 偏橙）——正红刺眼且与错误红同色相，朱红是账房/印章的传统「喜色」，69% 饱和度压住攻击性；跌绿取松绿（150°、27% 低明度）——鲜绿廉价感重且与「成功绿」国际惯例撞车；warning 压成赭珀（31% 明度）——正黄在白底上对比度无法达标（amber-500 仅 ~2:1），深琥珀棕是可读性达标的最浅解；绛红偏品红 17°——与朱红并置冷暖可辨，且品红向在国际上更常用于 error，双重线索。

**明度三轨**：① 信号主色统一 L 27–51% / 对比度 5–6:1（红绿并排视觉重量拉平）；② soft 底统一 L 93–95%（只给色相暗示，不抢内容）；③ soft 上 foreground 统一 L 21–40%，对底 ≥6.5:1。Dark 预案即三轨反转（主色 L 49–66%、soft 深色底、foreground 提浅），启用时按轨换算即可，不需重新设计。

### 1.3 状态 → Badge variant 映射

`badge.tsx` variant 全集：`default` / `secondary` / `success` / `warning` / `destructive`（soft 形态）/ `neutral` / `outline`。业务状态统一经 `getStatusBadgeVariant()`（`lib/utils.ts`）映射，禁止各页面自行发明状态色：

| 业务状态 | variant | 示例场景 |
|---|---|---|
| confirmed / active / success / passed / 启用 | `success` | 已确认交易、活跃组合、成功任务 |
| pending / running / partial_success / 在途 | `warning` | 待确认申赎/trade、跨天转移在途、运行中任务 |
| failed / error | `destructive` | 失败任务、数据缺失告警、校验未通过 |
| closed / draft / cancelled / 停用 | `neutral` | 已关闭组合、草稿、已撤销、禁用 |
| （操作型危险按钮，非 badge） | solid `bg-destructive text-white` | 删除、关闭组合等确认按钮 |

**无状态语义的标识**（买卖方向、管理员角色、资产名目 chip）不许占用状态色：方向/角色用「neutral badge + 彩色小圆点」或 `default` variant，圆点色取自 `lib/colors.ts`（见 §2）。

### 1.4 中性色体系（蓝灰底，冷静/可信）

| Token | 用途 | Light | Dark 预案 |
|---|---|---|---|
| `--background` | 页面底 | `#F7F8FA` hsl(220,23%,97%) | `#0F1420` |
| `--card` / `--popover` | 卡片/浮层底 | `#FFFFFF`（白卡浮于灰底，明度差第一层） | `#171E2C` |
| `--muted` | 次级底、neutral badge 底、hover 底 | `#EFF1F6` hsl(223,28%,95%) | `#232B3D` |
| `--border` / `--input` | 边框、分隔线 | `#E3E7EF` hsl(220,28%,91.5%) | `#2B3448` |
| `--foreground` | 一级文字 | `#1A2333` hsl(218,32%,15%) ≈15.9:1 | `#E9EDF5` |
| `--color-foreground-secondary` | 二级文字（表头、标签） | `#5A6577` hsl(217,14%,41%) ≈5.9:1 | `#A6B0C6` |
| `--muted-foreground` | 三级文字（12px 辅助、占位符） | `#64708A` hsl(221,16%,47%) ≈5.0:1（为 12px 辅助文字守 4.5 底线，**禁止再浅**） | `#8A94AB` |
| `--primary` / `--ring` | 主按钮、链接、选中态、focus 环 | = success `#2B5CD7` | `#7FA3F0` |

> 目标值已于 2026-08-29 一次性切换落地（此前为「shadcn 默认 slate 现状值 → 目标值」双轨渐进）；`--secondary` / `--accent` 底与 muted 同源同值。层级策略：页面灰底 → 白卡浮起 → 边框轻勾勒，三级明度差小、扁平金融风；文字三级 15/41/47% 明度。

### 1.5 护栏（ESLint）

`eslint.config.mjs` 内置 `no-restricted-syntax`（error），两道拦截（均含字符串字面量与模板字符串）：

1. **调色板类名**：禁止 `(text|bg|border)-(red|green|yellow|blue|amber|emerald|orange|purple|indigo|pink|teal|cyan)-<数字>`。新增颜色需求一律先加语义 token。
2. **任意值类名**（2026-08-29 起）：禁止 `text-[…]`（§5 四档之外）、`p/px/py/pt/pb/pl/pr-[…]`、`gap/gap-x/gap-y-[…]`、`rounded-[…]`（§7 派生档之外）。m 系 / space 系 / w·h 系任意值**暂不拦截**（多为视口比例或一次性尺寸，如 `h-[60vh]`、`sm:max-w-[500px]`，是否纳入待后续评估）。

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
| 表格数值单元格 | `number-cell` utility（`text-right font-mono tabular-nums whitespace-nowrap`，现状见 globals.css） | 右对齐 + 等宽数字 + 不折行（#394）。**漂移台账**：`lib/utils.ts` 的 `getNumberCellClass()` 返回**不含** nowrap 的三件套（固定串被 `utils.test.ts` 钉住），经 helper 拼类的表格须自行拼 `whitespace-nowrap`（投资人列表表现例，#693）；helper 全仓收敛会连带改变另一生产消费点 `SnapshotsContent` 的换行行为，留单独双端目检裁决，此前维持现状 |
| 无效值 | 各函数 `fallback` | 统一回显 `--`，不显示 `NaN` / `null` |

## 4. 占比精度分层

- **行级占比 1 位小数**：一律经 `largestRemainderPercents`（最大余数法，issue #99），全部行加总恒为 100.0%；禁止各处自行 `toFixed(1)`（会产生 ±0.1%×n 漂移）。**渲染口径**：返回值为百分比数值（如 `62.4`），展示统一 `percent.toFixed(1)%`（保底一位小数，`62.0%` 不缩为 `62%`）；**不要**走 `formatPercent`——其输入为小数形式、默认 2 位且带 `+` 号，口径不符。
- **分区头/聚合占比取整**：分区头、chip 合计由行级占比**加总后取整**，不再独立计算，保证同分区口径一致（issue #114）；名目 chip 合计恒显示，即使只有一行。
- 饼图图例直接展示行级加总的 1 位小数值（`buildAllocation` 输出），与分区头严格自洽。

## 5. 字号四级

| 级别 | 规格 | Tailwind | 用途 |
|---|---|---|---|
| 页面标题 | 24px / 600 / 1.3 | `text-2xl font-semibold` | page header；金额大字强调用 `amount-large`（同为 24px），不再新造规格 |
| 分区标题 | 18px / 600 / 1.4 | `text-lg font-semibold` | 卡片标题、持仓分区头 |
| 正文 | 14px / 400 / 1.6 | `text-sm` | 表格、表单、正文数值 |
| 辅助 | 12px / 400 / 1.5 | `text-xs` | 标签、时间戳、secondary 信息 |

配套规则：表格与右置明细的数值一律叠加 `number-cell`（统计卡主数值不套 `number-cell`，对齐见下条）；**只允许上表四档**——`text-base` / `text-xl` / `text-3xl` 等中间档与 `text-[Npx]` 任意值均属违规（新增代码由 ESLint 拦截，见 §1.5；存量 1 处豁免登记在 §1.5，改动页面时顺手收敛，不强制一次性清零）。

**统计卡（概览四卡）对齐**（#725 登记）：统计卡内**标签、主数值、注记同侧左对齐**，不因主数值是带符号/小数金额而右置——组合详情四卡（`PortfolioStatsCards`）与现金流水页概览四卡均为此**左对齐口径**（本条只约束对齐，不统一字号与字体）。现金流水页概览四卡的实现形态：主数值保留数字形态（`font-mono tabular-nums whitespace-nowrap`），拼类走 `getNumberCellClass()` 再以 `text-left` 覆盖（`cn`/twMerge 保证覆盖生效，该组合有断言钉住，见 `src/lib/utils.test.ts`），不直接挂自带 `text-right` 的 `number-cell` utility；字号桌面 `text-2xl`、移动 `text-lg`——移动 2 列栅格下 24px 的带符号大额会溢出压到邻卡、吞掉负号，故降一档，两档均在本节四档内、移动档与 `PortfolioStatsCards` 同档，不属 §6 禁止的「端侧独有字号」。**已知边界（#727 第二轮 L2 实测，隔离栈探针 390px 双列栅格、卡内容宽 146px）**：`text-lg` 下带符号 **7 位**金额需 151px、溢出 5px 落在列间距内不与邻卡文字相碰；**8 位**需 162px、与邻卡数值相接但不叠印；`text-xl` / `text-2xl` 分别叠印约 22px / 70px（数值互压不可读）；桌面 `text-2xl` 容器 215px 宽、8 位带符号 216px 刚好容纳不溢出。**失效条件**：降档只缓解不根治——8 位带符号量级在移动端仍与邻卡相接，彻底解决须改栅格或缩略显示，超出该轮范围、未排期；触及以上形态时按本条复核，不要只凭「已在四档内」判定合规。`PortfolioStatsCards` 现状只有 `tabular-nums`（`font-bold`，无 `font-mono` / `whitespace-nowrap`），**不因本条判为违规**，触及时再评估是否补齐数字形态。`number-cell` 的右对齐**只用于表格数值列（§8）与右置明细数值**（如 §12 对账明细紧凑列），不用于统计卡主数值；该 utility 挂在 inline / flex 子项这类**内容宽容器**上时（持仓卡主数值、现金流水移动卡金额行等既有写法）`text-right` 不产生可见右对齐，不属本条约束对象。**既有例外（待收敛，本轮不改）**：产品详情 / 平台详情 / 平台-产品详情三处概览卡的「持仓市值」大字（`ProductDetailContent.tsx:207` / `PlatformDetailContent.tsx:144` / `PlatformProductDetailContent.tsx:193`，行号以 #725 登记时为准，形态 `mt-1 text-2xl font-semibold number-cell`）仍为右置，后续触及相关页面时按本条收敛。

**字体栈（现状登记，2026-08-29）**：未自定义，走 Tailwind 默认栈——正文 `font-sans`（system-ui 系）、数值 `number-cell` 内 `font-mono`（ui-monospace/SFMono 系）。`tabular-nums` 依赖字体自带等宽数字特性，系统栈下各平台字形有差异（Windows 回退 Segoe UI / Consolas）；中文环境数字渲染一致性**未实测**。如需跨平台严格对齐的金融报表观感，后续可评估引入统一数字字体，届时在此更新决策。

## 6. 双端差异约定

- **同一份语义，两套壳**：移动端 `/m/` 薄壳页 + `MobileLayout`，业务内容走 `components/shared/` 共享组件（`variant: "desktop" | "mobile"` + `basePath` 适配）；颜色、数字格式、占比精度**双端完全一致，不允许端侧各自着色**。
- 布局差异只体现在：移动端网格列数更少（如统计卡 `grid-cols-2`）、表格优先改卡片列表、操作按钮收进图标/抽屉；**不发明移动端独有的色彩或字号**。筛选栏 / 日期区间 / 分页三类控件的端侧形态差异分别见 §9 / §10 / §11，本节只定原则。
- 新增共享组件时默认双端可用；确需端侧独立组件时放 `components/mobile/` / `components/desktop/`，但仍消费同一套 token 与格式化函数。

## 7. 间距 / 圆角 / 阴影体系

- **圆角**：统一走 `--radius` 派生档位——卡片/对话框 `rounded-lg`、输入与按钮 `rounded-md`、badge/chip/状态点 `rounded-full`、checkbox `rounded-sm`（基件规格登记见 §13）；禁用 `rounded-[Npx]` 任意值。
- **阴影**：扁平金融风，**卡片一律无投影**，靠「页面底 → 白卡 → 边框」三级明度差分层；投影仅用于浮层例外——Toast / 下拉 / Popover 用 `shadow-lg`，模态遮罩不动卡片本体。
- **内边距**：卡片内容桌面 `p-6`（CardContent 默认）、移动 `p-3`；区块间距桌面 `space-y-6`/移动 `space-y-4` 为既有惯例，新页面沿用，禁用 `p-[Npx]`/`gap-[Npx]` 任意值。
- **z-index（现状登记，2026-08-29）**：浮层统一 `z-50`（shadcn 基件默认：Dialog/AlertDialog 遮罩与内容、Select/Dropdown/Popover/Tooltip、移动端底部导航）；Toast 容器 `z-[100]`——**唯一例外**，须盖过模态层。不自定义其他层级；新增浮层一律复用基件，不手写 z 值。
- **focus 态（现状登记，2026-08-29）**：走基件默认 `focus-visible:ring`（`ring-ring` = primary 靛蓝，§1.4；button/checkbox/input/switch/tabs 已带），不自定义 ring 宽度/offset；自绘可交互元素必须保留等价 focus 可见反馈。

## 8. 表格规范

- **数字列右对齐 + `number-cell`**（等宽 tabular-nums；份额/金额/净值与整数计数列一视同仁），文本列左对齐，操作列右对齐（`text-right`）；列表头与数据列对齐方式一致（数字列表头同样 `number-cell`）。禁止对数值单元格手写裸 `text-right` 或内联 `font-mono tabular-nums`（#249 起 ESLint 拦截）；需与其他类拼接时（如 `cn("text-muted-foreground", …)`）用 `lib/utils.ts` 的 `getNumberCellClass()`。
- **表头**：`text-muted-foreground` 常规字重，不加底色、不加粗（层级靠字号与文字色，不靠底纹）。
- **斑马纹**：不用。行分隔靠 `border-b`，hover 行 `hover:bg-muted/50` 足够表达可点行。
- **空态**：表体空时表下方居中 `text-muted-foreground` 文案（或 `EmptyState` 组件），不渲染空表壳外的额外颜色。空态变体登记：① 数据空（默认）；② 筛选无结果——文案「无符合筛选条件的记录」+ 内嵌「重置筛选」入口（见 §9）；③ 无权限——文案「无权限访问本页」+ 返回入口按钮（`EmptyState` action 位）。
- **加载态**：表体区域居中 `Loader2 animate-spin` + `text-muted-foreground`，不清空表头。此为首次全量加载形态；筛选/翻页等局部刷新的加载态见 §14。
- **主次双行单元格**（#124 起）：单元格允许「主行 + 次要行」复合结构——主行 `text-sm` 正文色，次行 `text-xs text-muted-foreground`（§5/§1.4 既有档位，不新造）；适用场景登记：name + code、产品名 + 市场后缀。双端一致均渲染双行，移动端不裁剪次行。下拉选项内不用双行——用单行 `name (code)`（LOF 附市场后缀，如 `name (code · 场内)`），与既有表单下拉先例一致；#705 起括号内取**显示码**，港互认下拉只显 6 位销售码、不并列双码（下拉行宽有界，双码并列是详情页语义）。**同属「单行 `名称（码）` 标签」这一类的还有确认/编辑弹窗的产品名行**（`formatProductName()`：调仓确认 / 到账 / 编辑抽屉、份额事件确认 / 编辑），港互认同样只显销售码、不并列双码，是**有意取舍**而非漏改（#705 L2 评审 Suggestion 1 复核后维持）：① 复用 `formatProductCodeLabel()` 会拼出 `名称（968050（1001767344））` 的嵌套括号，另造 `名称（968050 / 1001767344）` 则是第三种码标签形态，与「并列与否由 `formatProductCodeLabel()` 单点决定」相冲；② 弹窗行是 `justify-between` 的左标签右值、移动端 390px 下值域已含长基金名，双码并列会挤压换行；③ 核对所需的机器码并未丢失——弹窗正是从列表行打开的，同一屏的 `ProductCell` 次行与详情页副行都是双码并列，#705「与 10 位码同时可见或明确标注」与待决第 3 条「界面某处保留完整原始码」由这些站点满足。若日后要改，需同时定新标签形态并做双端目检，不在展示码这轮内。**字重层级**（#355 登记）：产品列主行加 `font-medium`（产品是行内主标识），平台/投资人列主行不加粗——两者字重差是刻意层级、非漂移，勿「修平」。共用实现：`components/shared/NameCodeCell.tsx`（name + code）、`components/shared/ProductCell.tsx`（产品名 + `显示码 · 市场名`，#355；market 为空不拼后缀，避免 CASH/在途虚拟产品出现 `· --`）。**代码位取显示码而非机器码（#705）**：非港互认去掉 `.SH`/`.SZ`/`.OF` 后缀（`510300.SH` → `510300`），港互认显 `6位销售码（10位机器码）` 双码并列（`968050（1001767344）`，销售码缺失回落单显机器码）；行级/产品级有后端 `display_code` 时优先消费，缺失走 `lib/utils.ts` 的 `formatProductCodeLabel()` 单点兜底。**只有显示层换码**：href、`data-code`、React key、CLI 入参等标识位一律保持机器码。
- **并列双行单元格**（#355 起）：语义**并列**（非主次）的两个同类值可上下双行，**两行同 `text-sm` 同正文色**，不做字号/色分层——区别于上条主次双行，两者是不同模式，按值间关系选用。顺序恒「**上行 = 先发生、下行 = 后发生**」，表头文案与行内上下顺序对应（如「交易/确认日期」「权益登记/除息日」）；每行挂原生 `title` 标注语义标签；任一行空值仍占位 `--`；两值相同（如场内当日确认 `trade_date == confirm_date`）仍渲染两行、不折叠，以保持列结构稳定；两行均 `whitespace-nowrap`，防窄列下日期按连字符断行。上下行槽位由调用点固定传入，**组件内不按值排序**（下行可能是 `--` 空值占位）。适用场景登记：交易/确认日期、申请/确认日期、权益登记/除息日。**唯一例外**：pending 记录的确认日是预计值，在下行内联 `text-xs text-muted-foreground`「预计」后缀——合并后单元格已占两行，「预计」不能再起第三行。共用实现 `components/shared/DatePairCell.tsx`。
- **结对行（父子行）**（#126 起）：成组记录（基金腿 + 现金腿）主行正常渲染；子行首列缩进一档（`pl-8`）、整行 `bg-muted/50`、内容降一档 `text-xs`（数值仍右对齐 `number-cell`）。主行去下边框（`border-b-0`）使主+子视觉成组，子行保留下边框作组分隔。操作按钮只在主行，子行不单独响应 hover/点击。子行文案模板：「现金扣款 · 平台名」/「现金到账 · 平台名」/「**现金待到账** · 平台名」（中点 `·` 分隔）；落单现金行：「现金 · 业务来源」（如「现金 · 申购确认」）。**「现金待到账」的判据（#493）**：调仓卖出确认时会建一条 `confirmed` 但**到账日在未来**的 CASH buy 腿（`trade_date` = 基金确认日 C、`confirm_date` = 到账日 A，缺省 A=C）——主行状态讲的是**基金腿**的已确认，子行若照旧写「现金到账」会把尚未到账的钱说成已到账。故子行须按**现金腿自身的状态 + 生效日**判定：`confirmed` 且生效日不在未来才是「现金到账」，其余（未来到账、或到期却仍未确认）为「现金待到账」。**子行内容格（#493 起）**：金额之后空占位由 `colSpan={3}` 折叠，随后是**现金腿生效日**内容格（买入=扣款日 T、卖出=到账日 A），与主行「交易/确认日期」列对齐；末段再以 `colSpan={2}` 占满状态与操作列。**子行金额禁用涨跌色**（§1.1）——扣款 `-`、到账 `+` 符号表意，`text-foreground`；符号由展示层按 `trade_type` 推导，不回写数据。**子行空占位以 `colSpan` 折叠**（#355 起，frontend/ 内 `colSpan` 首次使用）：子行槽位与主行列数是纯位置耦合、tsc/lint 拦不住，连续空单元格折叠为 `<TableCell colSpan={n} />` 后形状一眼可核，span 写错会立刻视觉暴露而非静默错一列；改主行列数后须核对「首列标签 + Σspan + 内容格 == 主行列数」。
- **派生结对子行（对手腿被筛掉时）**（#719 起，「只补信息、不补行」的渲染端）：按产品/平台/状态筛选后，同组对手腿可能不在结果集里，此时用列表响应上的只读派生 `paired_leg`（口径见[业务规则·调仓](../reference/business-constraints.md#rule-trade)）**合成**子行。视觉形态与真实子行逐项一致，不新造第二套：同一副 10 列槽位算式 `1 + 2 + 1 + 3 + 1 + 2`（首列标签 + 平台·类型 + 金额 + 份额·手续费·价格 + 生效日 + 状态·操作）、`pl-8`、整行 `bg-muted/50`、`text-xs`、**无操作入口**、不响应点击。四条边界：
  - **不计入 `total`、不算一行数据**：它是主行的附属说明，翻页与计数契约不变；同组对手腿**已作为真实行**出现在本页时（判据 = `paired_leg.id` 在页内 id 集合中）**不渲染派生子行**，一组现金信息只出现一次。
  - **首列文案按对手腿类型分档**：对手是基金腿 → `↳ 买入/卖出 产品名（对手腿状态）`（产品名取后端 `product_name`/`display_code`，缺失走 `formatProductCodeLabel()`）；对手是另一条现金腿（跨平台转移）→ `↳ 转出/转入 平台名`，转入腿**尚未到账**时补 `（现金待到账）`（#493 口径：pending 或生效日在未来即未到账；已到账不带后缀，`（已确认）` 与主行状态列重复）；申赎腿（`sub_` 组）后端恒 `null` → 主行保持「现金 · 申赎确认」，**不补对手信息**（对手方是 subscription 记录，主体在申赎页）——刻意边界。`↳` 前缀标明「这是另一条腿」而非本行的现金子记录。**派生 pair 的子行一律走这一档**，含对手是现金腿的平台间转移（#721 评审 S1）——`现金扣款/现金到账 ±` 那套模板只用于结果集里的**真实现金腿**子行，两套形态不因筛选而互换。
  - **主行产品列不换**：派生只**加子行**，主行本身是现金腿时产品列仍是 `cashOrphanLabel` 的业务来源标注（「现金 · 调仓」等），不改成 `现金类资产` 的 ProductCell——筛现金要读的是资金去向，不是产品名（接线在 `TradesContent` 的 `keepCashSourceLabel`，仅派生 pair 生效；真实转移对「双 CASH，sell 为主」形态照旧）。
  - **金额不加 `+`/`-` 前缀**：前缀是资金流向语义、只属于现金子行；派生子行的金额格是对手**基金腿的 `amount`**（买入=扣费后净投入、卖出=成交额，#173/#182），与主行现金腿的实扣实到是两套口径，不并存、不互相冒充。份额·手续费·价格合并槽位（`colSpan={3}` `text-right`）只在对手是基金腿时填 `N 份 @ 净值 · 费 ¥X`；现金腿无份额无净值语义，该槽位留空。
  - **生效日槽位**：对手腿为卖出方向时前缀「到账」（与 #493 现金子行同口径），其余直接显日期；`confirm_date` 缺失时**留空**，沿用 #493 现金子行的既有形态——这是 §12「空值占位统一 `--`」在子行补充槽位上的既存先例登记（真实数据的 `trade.confirm_date` 创建即按 `confirm_days` 定值，缺失只出现在异常存量），不是新的空值口径，勿据此把主行数据格的 `--` 改成留空。
  - 共用实现：文案与判据单点在 `lib/tradePairs.ts`（`cashRowLabel` / `counterpartSubLabel` / `counterpartDateText` / `tradeCardDetailLine`），三处同形交易卡（`ProductDetailContent` / `PlatformDetailContent` / `PlatformProductDetailContent`）与交易列表都从这里取——现金行的副行曾各写一份 `shares !== undefined` 判据，把现金腿渲成 `-- @ 1.0000`（虚假净值暗示净值型产品），收敛到单点后由 `tradePairs.test.ts` 钉住。
- **多行合并段（rowSpan 组列 + 余额列）**（#725 起，现金流水页桌面 7 列首个落地）：相邻同 `(平台, 现金日期)` 的多条流水属**同一平台同一日**的一段，首列「日期 · 平台」与第 5 列「当日余额」各以 `rowSpan={段内行数}` 纵向合并成一格——余额是**日终存量**、整段共用一个值，逐行重复既冗余又会被读成「每笔都改变了余额」。分组键与跨度由 `lib/cashFlows.ts` 的纯函数单点决定（`cashFlowGroupKey()` = `` `${platform_code}|${cash_date}` ``、`balanceSpans()` 线性扫描、被覆盖行返回 `-1`），组件只消费不另算。合并格按跨度取垂直对齐——`rowSpan>1` 的合并段在段内垂直居中（`align-middle`），筛选打散退化为单行（`span=1`）时与逐行列一致顶对齐（`align-top`）；非合并列（流水类型 / 来源·去向 / 金额 / 状态 / 操作）逐行照常 `align-top` 渲染。**跨度按屏上实际行数算**，故按流水类型/状态筛选把一段打散后自然退化为多个单行格（span=1），不会错位；也因此**余额值与筛选无关**——它取的是后端派生的日终余额，不是屏上金额累加。列数核对仍走 #355 口径：改主行列数后核对「组列 + 4 常规列 + 余额列 + 操作列 == 7」。**移动端不合并**：卡片流每卡自带「平台 · 日期」与余额，重复是可接受的（纵向滚动无表头列对齐约束），不搬 rowSpan。共用实现 `components/shared/CashFlowsContent.tsx`。

## 9. 筛选栏规范（filter bar）

> 流水类列表页（申赎/调仓/快照等）的标准配置，全站首个落地为 #125/#126。本节只定跨页一致决策，控件本身读 `components/ui/`。

- **容器**：置于表格上方、与表格同卡片内容区顶部，不单独卡片包裹；横向 `flex flex-wrap`、控件间距 `gap-2`，与下方表格的间距走 §7 区块档位。
- **控件尺寸**：筛选栏控件统一紧凑档 `h-9`（Select/Input/日期触发按钮同高）；表单对话框内仍为 `h-10`，两档不混用。**已知例外**（2026-08-29 登记）：`h-9` = 36px 低于移动端 44px 触控目标建议值——筛选栏为桌面优先的密度场景，移动端筛选面板内控件现状同样沿用 `h-9`；后续做触控专项时移动端面板内控件升 `h-10` 即可（桌面不变），不在本次一刀切。
- **标签**：筛选栏省略 `Label`，以 placeholder 表意；placeholder 统一「全部 + 维度名」（全部状态/全部平台/全部产品）。默认有值的筛选（如"最近 1 年"）显示实际值而非 placeholder。
- **控件排序**：全站统一——时间区间 → 状态 → 实体维度（投资人/平台/产品）→ 类型；多页并存时顺序一致。
- **生效方式**：变更即时查询，不设「查询」按钮；文本输入类防抖 300ms，下拉/日期选择即时生效。
- **重置**：单项清空用控件自带清除件（如 date-picker 的 X 先例）；存在非默认筛选时，筛选行末尾出现「重置」ghost 按钮，点击恢复默认筛选集。
- **激活提示**：桌面端控件值即提示，不额外加徽标；移动端「筛选」入口按钮带激活计数 Badge（`default` variant——纯计数无状态语义，§1.3）。
- **移动端形态**：筛选控件收进「筛选」折叠面板，不常驻平铺；展开后纵向堆叠 `grid-cols-1`。
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
- **金额**：带 `¥`、千分位、2 位小数；概览大字可用 `formatCompactCurrency` 的万/亿紧凑格式（`¥X.XX 万` / `¥X.XX 亿`），表格内不用紧凑格式。
- **空值占位**：统一 `--`（各 format 函数 fallback），禁止 `N/A`、`null`、空字符串上屏；JSX 内禁止手写 `-`/`"--"` 字面量当占位——空值判断交回 format 函数（`null`/`undefined`/空串/`NaN` → fallback `--`），不要用 truthy 三元短路（真 0 会被误判为缺失，如 `fee=0` 应显示 `¥0.00`；#249 起 ESLint 拦截 JSX 内 `-`）。
- **单位**：份额数值后带「份」（一律 `formatSharesUnit`，见 §3）、金额不重复写「元」（`¥` 已表意，例外见下条）；图表 tooltip 中金额可带「元」补语义。
- **概览大字「数字 + 元」例外**（#249 登记，设计来源 #99/#114/#117）：概览大字与分区/分组合计允许 `formatNumber(...)` + 手写「元」后缀（适用清单：`PortfolioHoldings` 大类分区头合计/维度 chip 合计、`PortfolioStatsCards` 统计卡），大字区「数字 + 元」比 `¥` 更紧凑、不与表格内 `¥` 争视觉层级。**边界**：仅覆盖上列概览大字与分区/分组合计；表格数值单元格、卡片小字明细不在例外内，仍走 §3 format 函数（收益列的无 `¥` 形态见下条 #595 例外）。**规范与实现二选一**：若未来决定代码侧统一为 `formatCurrency`，须先撤回本条例外，不允许两者并存。
- **持仓卡片收益列无 `¥` / 平台卡现金段 0 位小数例外**（#595 登记，设计来源 D1/M1/D2/M2）：`HoldingProductCard` 累计收益/最新收益列按设计稿为纯数字带符号（`+980.00`，正负号展示层拼、数值走 `formatNumber` 系），同卡市值大数字仍带 `¥`；`HoldingPlatformCard`「N 只产品 · 现金」段按设计稿大额取 0 位小数（`¥40,000`），负现金为真实存量、不省略且符号内显。**边界**：仅覆盖上述两个卡片列；其余金额展示仍走 §3 format 函数（2 位小数带 `¥`）。**规范与实现二选一**：若未来改回带 `¥`/2 位小数，须先撤回本条例外。
- **现金流水页金额符号三档例外**（#725 登记，设计来源 mockup `fmtMoney`/`fmtNumber`；#727 评审对齐实现）：现金流水页按「存量 / 流量 / 对账明细」分三档取符号与 `¥`，前两者由 `lib/cashFlows.ts` 的纯函数单点持有、第三者复用既有函数，均建在 `formatCurrency`/`formatNumber` 上（不散装 `toFixed`）——① **存量**（可用现金、在途、平台日终余额、构成表余额列、对账块与投影块的期初/日终/合计行）走 `formatBalanceCurrency`：**负号在 `¥` 前**（`-¥1,234.00`）、正数**不补 `+`**（存量是「有多少」不是「涨跌」，`+` 会误读成收益）、空值 fallback `--`；② **流量**（本期流入/流出、金额列、构成表流入流出列）复用 `lib/utils.ts` 既有的 `formatSignedCurrency`（现金流水页不另造同名函数）：**± 在 `¥` 前**、正数补 `+`（`+¥5,000.00` / `-¥3,200.00`），与 #638 L2 S2「正负号不得分居 ¥ 两侧」同源；金额列有一个例外——**重估行**（`is_override`，绝对值覆盖不是增量）走 ①（`cashFlowAmountText()` 单点分派），带 `+` 会把「余额被覆盖为 X」误读成「流入 X」；③ **对账明细裸数字**（对账块内逐笔「当日已生效」行、投影块「本笔到账」行）走 `formatBareSigned`：**带 ± 不带 `¥`**（`+5,000.00`），对齐 mockup 明细行右对齐的紧凑列——只覆盖这两类**增量明细行**，块内的期初/日终/合计是存量、仍走 ①。**边界**：仅覆盖现金流水页；存量档的「正数不补 `+`」是本页相对全站 `formatSignedCurrency` 的**唯一新增取舍**（存量非涨跌），其余两档都复用既有惯例。**规范与实现二选一**：若未来统一为全站 `formatCurrency`/`formatSignedCurrency`，须先撤回本条例外。
- **净值口径回退的屏上标注**（#703 登记）：产品详情页与平台-产品详情页的净值曲线卡与区间收益率卡标题改由响应字段 `nav_basis` 驱动——`accumulated` 维持原文案（「累计净值走势」/「区间收益率」），`unit` 在净值源改「单位净值走势」、在场内改「收盘价走势（市价）」/「区间收益率（市价）」；回退口径下卡标题后加一行 `text-xs text-muted-foreground` 注记（形态同 #641 脚注），写明不含分红再投资、场内为市价含折溢价。文案单点在 `src/lib/navBasis.ts`（两页共用、防副本漂移，colocated vitest 钉标题与注记）。**边界（失效条件）**：空态与失败态判序不变——曲线与六窗皆空才落「暂无净值数据」+ 全「--」，此时仍报 `accumulated`、不加注记；`aria-busy` 局部加载态与失败臂位置均不动。E2E 双页同批改（`product-detail.spec.ts` 三条：回退口径、皆空空态、累计净值口径不加注记；`platform-detail.spec.ts` 一条同形），双端各跑。
- **口径注记分色**（#595 登记）：带 `*` 的口径说明注记（如「累计收益* = …」）是「口径与直觉不同」的警示，走 `text-xs text-warning`（琥珀警示语义）；有数据时恒渲染，空态走「暂无」分支不渲染。先例：`ProductDetailContent` 累计收益* 注记、`PortfolioInvestorsList` 投资人累计收益注记（形态同前者）。**区别于中性口径脚注**（#641 在途脚注、本条上款的 #703 净值回退注记）——纯口径补充、无警示语义的一律 `text-xs text-muted-foreground`，两种注记不混用。
- **平台详情页持仓明细区的在途口径脚注**（#641 登记）：`*持仓市值含在途资金 ¥N，上方卡片不含在途`（`text-xs text-muted-foreground`，明细网格后），仅本平台在途 > 0 时渲染；数字必须取该平台 by-platform 卡的平台级 `in_transit_market_value`（无在途为 0.0），**不得借组合级字段**——多平台组合会把 A 平台的在途写到 B 平台名下（PR #639 B2）。E2E 见 `platform-detail.spec.ts` 在途脚注用例。

## 13. 组件复用红线

- 新 UI **先查 `components/ui/`（基件）与 `components/shared/`（业务件）**，能复用不新造；状态徽标一律 `Badge` + variant（#127 的 9 处手写 badge 收敛为首个执行案例），禁止再出现 `inline-flex items-center rounded-full … bg-*-100 text-*-800` 手写件。
- 双端页面优先 `components/shared/` + `variant`，只有布局本质差异才拆 `components/mobile/` / `components/desktop/`。
- 弹窗一律走 `Dialog` / `AlertDialog` 基件；提示一律走 `Alert` / toast，不自绘浮层。
- **待建基件登记**（规格已定、随首个使用页面落地；未建前禁止手写同功能件）：`checkbox`（#146 首个使用，shadcn 基件，`rounded-sm`、选中态 `bg-primary`）、`date-range-picker`（#125，形态见 §10）、`pagination`（#125/#126，形态见 §11）。

## 14. 交互反馈模式

- **加载态二选一**：区块级 = 居中 spinner（`Loader2 animate-spin` + muted 文案，用于表格/卡片/整页加载）；按钮内 = `Loader2` 替换前缀图标 + `disabled`（用于提交/确认/同步等动作）。**现状以按钮内为主**，新代码动作类加载一律按钮内，区块级仅限首次数据加载。
- **查询失败态 ≠ 空态**（#647/#655 定形、#663 收敛）：读侧查询失败一律渲染共享 `components/shared/QueryErrorState.tsx`（「加载失败：{后端 `detail.message`}」+「重试」链接按钮），**不得**用 `EmptyState` 或裸 `<p>` 承载，更不得让失败落进空态文案——「取不到」被说成「没有数据」会把后端故障读成业务事实（净值走势卡曾如此）。**本条「一律」只管页面与区块级**：Dialog 内的读侧失败走 destructive inline Alert +「重试」（`CashMarketValueUpdateDialog` 的覆盖记录块），按本节「错误提示分工」属阻断性提示，同样透出后端 `detail.message`——是既存分工而非漏改，不要为「统一」把它换成 `QueryErrorState` 而丢掉 destructive 语义。形态选择：卡内传紧凑 `className`（现口径 `mt-3`），页级/区块级用组件缺省 `py-8 text-center`（与 `EmptyState` 同尺寸，不新造视觉语言）。§8 的三种空态变体不含失败态——失败不是「空」的一种。
- **局部加载态**（筛选变更/翻页等局部刷新，#125/#126 起）：保留旧数据，表格容器 `opacity-50` + 右上角 `Loader2` 小 spinner；不切换为空态或区块级 spinner，避免闪烁与布局跳动。首次进入页面的全量加载仍走区块级 spinner（§8）。**卡片数据区的重取同理（#683 D-1）**：同一区块容器 `opacity-50` + 卡片右上角 `Loader2`，并把该状态显式写成 `aria-busy="true"/"false"`——定位器契约禁止按 Tailwind 工具类定位，故这个属性同时是状态语义与 E2E 锚点（手法同 #650 的 `aria-current`）。这里**不得**为消除「旧值看起来像当前值」的观感而删掉 `placeholderData: keepPreviousData`（那是筛选/翻页不闪烁的既有行为，属另一项业务判定）；也不得只看数值是否变旧来判——`isRefetching` 为真即标 busy，首屏在途不标。
- **操作区按钮层级**：每卡片/区块主操作唯一（`default` primary solid），其余 `outline`/`ghost`；危险操作与主操作分区放置，不与主操作并排。
- **错误提示分工**：操作结果（增删改/同步/确认的成败）用 **toast**（success/error/warning/info 四型，映射 success/destructive/warning/success 色系，info 不单独设色）；表单校验、数据完整性、阻断性提示用 **inline Alert**（`default` / `destructive` 两 variant；校验通过/警告用 success/warning 的 `bg-*-soft` + `border-*/30` 组合，不新增 Alert variant）。
- **后台任务进度**（sync-job 类，#146 起）：进行中 = 状态 Badge（`running`→`warning`，§1.3）+ `Loader2` + 文案的行内区块；不引入 Progress 条组件（任务无可靠百分比，YAGNI）。终态反馈仍走上述 toast 分工，页面数据随之刷新。
- **危险操作**（删除、关闭组合、强制操作）统一 `AlertDialog` 二次确认，确认按钮 solid `bg-destructive text-white`；普通确认走 `default`（primary 靛蓝）。
- **破坏性操作两段式确认**（dry_run → confirm，#146 起）：第一步预览 AlertDialog 列出影响范围，文案模板「将删除 X 张快照（YYYY-MM-DD ~ YYYY-MM-DD），此操作不可恢复」；确认按钮沿用 solid `bg-destructive text-white`；拿不到预览结果时禁止直接执行。
- **动效（现状登记，2026-08-29；克制原则）**：全站仅三类动效——① 加载 spinner（`Loader2 animate-spin`）；② toast 滑入（globals.css `slideIn` keyframes，唯一消费点 ToastContainer）；③ 基件内置过渡（tailwindcss-animate `animate-in/out`、hover/状态切换 `transition-colors`）。不新造关键帧、不引入滚动/视差动效——金融界面以静为默认。

## 15. 图表表达规范

- **饼图扇区从大到小排序**（资产分布即按市值降序），色板按 `CHART_COLORS` 顺序消费；**超过 6 类必须合并为「其他」**，这是规范条目而非建议。合并项用色分场景（§2）：资产分布用 `OTHER_COLOR`（C8 深灰蓝，与现金 C7 区分）；无「现金」语义的普通多序列用 `CHART_OTHER`（C7 灰蓝）。
- **图例位置**：环形图左图右例（移动端上下堆叠），图例 = 色点 + 名称 + 占比（1 位小数，§4）；不单独发明图例样式。
- **tooltip**：金额 `${formatCurrency(value)}`（可带「元」补语义）、净值 `formatNav`、占比 1 位小数；tooltip 内涨跌数值用 `text-gain`/`text-loss`，图表元素本身（线/柱/扇区）永不用涨跌色。
- **折线**：净值主线恒 `NAV_LINE`（C1 靛蓝）、无数据点圆点（`dot={false}`）、参考线用 `CHART_OTHER` 灰蓝虚线；多序列按 C1→C8 顺序取色。

## 16. 图标体系与着色

- **图标库**：全站统一 `lucide-react`（49 处消费，唯一图标来源）；不引入第二图标库、不内联自绘 SVG 图标。
- **尺寸档（现状登记，2026-08-29）**：默认 `h-4 w-4`（16px，与 14px 正文配套）；移动端导航/大触面 `h-5 w-5`（20px）；紧凑徽章内 `h-3.5 w-3.5`（14px）。三档之外不新造。
- 图标默认继承文字色（不加颜色类）；需要语义时与同行文字共用同一 token：成功 `text-success`、警告 `text-warning`、失败 `text-destructive`、涨跌图标随数值走 `getReturnColorClass`。
- 状态指示小圆点（通知级别、交易日标记、方向标识）用对应 token 的 solid 色（`bg-success` / `bg-warning` / `bg-destructive`）或 `lib/colors.ts` 分类色，不用 `-soft`。

## 17. Dark mode 启用条件（暂缓）

token 已备双套值，启用前必须完成：⓪ **先对齐双通道**——`globals.css` `.dark` 的中性色现状仍为 shadcn 默认 slate 值（如 muted `217.2 32.6% 17.5%`），**并非** §1.2/§1.4 登记的 dark 预案值，须先按预案值改写 `.dark` 再进入实测；① 实测全部 dark 预案值对比度（当前为同构推算）；② 存量 `slate/gray` 中性类名与任意值色（`[#xxx]`）清理；③ 图表色板暗色适配评审。未满足前禁止在代码中加 `dark` 类或 `dark:` 变体。

## 18. 存量债与迁移策略

- 本规范落地时已完成：语义 token、Badge variant、盈亏色函数、图表色板、全部 `(text|bg|border)-调色板-数字` 类名清零（#127）；中性色目标值一次性切换（2026-08-29，§1.4）。
- 已登记、渐进收敛的存量：中间档字号与 `text-[Npx]` 任意值（1 处 / 1 文件，ESLint ratchet 豁免见 §1.5）、w·h 系一次性尺寸任意值（如 `h-[60vh]`、`sm:max-w-[500px]`，未拦截、不强制清理）、`slate/gray` 中性类名、快照页原生 `<input type="checkbox">` 手写件（#146 收敛为 §13 登记的 checkbox 基件）。原则：**改动到该页面时顺手替换，不单独开重构 issue**。
- 已登记、**不适用**「顺手替换」原则的存量：~~桌面页面标题 `<h1>` `text-3xl font-bold tracking-tight`（12 处 / 12 文件）~~ **已于 #386 一次性整体收敛为 §5 页面标题档**（`text-2xl font-semibold`，含 4 个共享组件移动分支的 `font-bold` 字重同步对齐，2026-09-05）。~~收敛时核对移动端发现新漂移：3 处移动端专属薄壳页 h1 不符 §5~~ **已于 #394 判定为非刻意紧凑形态并一次性对齐 §5 页面标题档**（`app/m/dashboard` 字重 `font-bold`→`font-semibold`；`app/m/portfolio/[code]` 与 `positions` `text-xl font-bold`→`text-2xl font-semibold`。判定依据：#386 已将共享组件移动分支收敛为 semibold，此三处为全站仅剩孤例，且 §6 不发明端侧独有字号，2026-09-06）。
- **无定义的类名不照抄**（#650 登记，#658 核对）：`BottomNav` 的 `safe-area-pb` 是全仓无定义的空类名（Tailwind 无该 utility、`globals.css` 无该定义），既存而非 #658 引入；它已从「取较新一套样式」的理由里撤下。改动该组件或照它的写法复制时**不得**把它当既有约定扩散到别处——空类名不报错、lint/tsc/build 都抓不到，只会静默失效。

---

## 19. 新页面 / 大改自检清单

提交前对照（全部「是」方可合入）：

1. 颜色只出现语义 token 与 `lib/colors.ts` 导出，无调色板类名、无组件内 hex 字面量（ESLint 已过）。
2. 涨跌数值色只来自 `getReturnColorClass` / `getReturnBgClass`；badge 状态色经 `getStatusBadgeVariant()`，未占用涨跌色（§1.1/§1.3）。
3. 数字全部经 `lib/utils.ts` 格式化函数；表格数值列右对齐 + `number-cell`，统计卡主数值与标签/注记同侧左对齐（§5）；占比走 `largestRemainderPercents`，无散装 `toFixed`（§3/§4/§8）。
4. 字号只用四档（24/18/14/12）；无 `text-[Npx]`、`p-[Npx]`、`gap-[Npx]`、`rounded-[Npx]` 任意值（§5/§7）。
5. 卡片无投影（浮层除外）；圆角走 `--radius` 派生档；间距走既有档位（桌面 `p-6`/`space-y-6`、移动 `p-3`/`space-y-4`）（§7）。
6. 表格：表头 muted 常规字重、无底色无斑马纹；空态 / 首次加载 / 局部刷新加载按 §8/§14 既有形态，不自造。
7. 筛选栏 / 日期区间 / 分页如使用，形态与控件排序符合 §9–§11（控件 `h-9`、时间→状态→实体→类型、即时生效、无结果空态带重置入口）。
8. 状态徽标一律 `Badge` + variant；无手写 `bg-*-100 text-*-800` 件；弹窗走 `Dialog` / `AlertDialog`，提示走 toast / `Alert`（§13/§14）。
9. 图表：色板按序消费、净值主线 `NAV_LINE` 不用红绿、超 6 类合并「其他」（场景用色见 §15）、图表元素本身不着涨跌色（§2/§15）。
10. 双端同语义：共享组件 + `variant` 适配，未发明端侧独有色彩或字号（§6）。
