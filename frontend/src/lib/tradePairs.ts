import type { Trade, TradePairedLeg } from "@/types/trade";
import {
  toDateOnly,
  getTradeStatusLabel,
  formatProductCodeLabel,
  formatDate,
  formatSharesUnit,
  formatNav,
} from "@/lib/utils";

/**
 * 调仓列表结对视图行（#126 决策⑧）：
 * pair = 主行（基金腿 / 现金转移 sell 腿）+ 子行（配对现金腿 / 转移 buy 腿）；
 * single = 普通单行，或配对腿被筛选/分页排除后的孤儿回退。
 *
 * `derived`（#719）：子行数据来源。false = 同组两腿都是结果集里的真实行；
 * true = 对手腿被筛选/分页挡在外面，改用后端只读派生快照 `paired_leg` 合成。
 * 两者渲染同一副列位，差别只在派生子行**不是**可导航的真实行（不给操作入口、
 * 不计入 total），所以分支只影响数据来源，不影响布局。
 */
export type TradeRow =
  | { kind: "pair"; main: Trade; sub: SubLeg; derived: boolean }
  | { kind: "single"; trade: Trade };

/**
 * 子行数据形状：真实腿（`Trade`）与派生快照（`TradePairedLeg`）共用。
 * 直接取别名而不是重造一套字段名——两个来源字段语义完全一致，改名会让
 * 「真实优先、派生兜底」这条规则在类型上失去可核对性（`Trade` 结构上可赋给
 * `TradePairedLeg`，缺的 `trade_date`/`notes`/`actual_amount` 子行本来就不消费）。
 */
export type SubLeg = TradePairedLeg;

const CASH_CODE = "CASH";

// 交易 CASH 腿按 product_code 判定，不套用快照现金行判据；见[现金账本](../../../docs/reference/business-constraints.md#rule-cash)。
// 入参取结构子集而不是 `Trade`：真实腿与派生快照都要判（#719 子行两种来源共用）。
export function isCashLeg(trade: Pick<Trade, "product_code">): boolean {
  return trade.product_code === CASH_CODE;
}

/**
 * 已确认卖出行的「修改到账日期」窄入口是否适用（#525）：只给**基金**卖出腿。
 *
 * CASH 卖出腿（赎回配对 `sub_` 腿、当天完成的现金转移主腿）不是窄例外的设计对象：
 * ① 后端 PUT 对 CASH 腿的非 notes 字段一律 `CASH_TRADE_FORBIDDEN`；② 读侧派生的
 * `cash_confirm_date` 只填在基金腿上（CASH 腿自身恒 null）→ 弹窗预填恒空、保存钮
 * 恒禁用，而手选日期提交必被 ① 拒绝（死路入口）。这类行回到「先取消确认」分支。
 */
export function canEditArrivalDate(trade: Trade): boolean {
  return !isCashLeg(trade) && trade.trade_type === "sell";
}

/**
 * trades → 结对行。顺序敏感：分组与输出均保持传入顺序（= 后端
 * trade_date DESC, transfer_group, id DESC 排序序，决策⑪保证同组相邻）。
 *
 * 规则（边界即契约，异常形态显式回落 single、不静默错渲成现金子行，#507）：
 * 1. 按 transfer_group 分组；
 * 2. `sub_` 前缀组（申赎配对现金腿）→ 恒 single，主体在申赎页；
 * 3. 组内恰 2 条且恰 1 条 CASH → pair（非 CASH 主、现金子）；
 * 4. 组内恰 2 条且均 CASH → pair（sell 主、buy 子；现金跨平台转移）；
 * 5. 其余（孤儿单腿、异常多条、恰 2 条但两腿均非 CASH、双 CASH 而缺 sell 或 buy）
 *    → 全部 single 回退，不错行不空白。
 *
 * 规则 3/4 之外的 2 腿形态不可由后端数据到达（rebal_ 恒「基金腿 + CASH 腿」、
 * 跨平台转移恒「CASH sell + CASH buy」、sub_ 恒单腿），兜底只为异常数据不产生错行。
 *
 * 规则 6（#719，只作用于落到规则 5 的行）：single 行若带后端派生对手腿
 * `paired_leg`、且该对手腿**不在**本页结果集里 → 升级为派生 pair（derived=true）。
 * 这就是「筛产品后仍看得到配对现金腿 / 筛现金后看得到对手基金腿」的实现路径：
 * 补的是信息，不是行——`total`、分页与排序都不受影响。
 */
export function groupTradeRows(trades: Trade[]): TradeRow[] {
  const groups = new Map<string, Trade[]>();
  for (const t of trades) {
    // transfer_group 后端 NOT NULL；前端类型可选，缺省时按 id 自成一组走 single 回退
    const key = t.transfer_group ?? `__none_${t.id}`;
    const g = groups.get(key);
    if (g) g.push(t);
    else groups.set(key, [t]);
  }
  // 真实优先、派生兜底的判据：对手腿只要本身就在结果集里，就绝不再用派生快照
  // 重复渲一遍（异常数据下同组两条非 CASH 腿会互相指认，靠这个集合挡住）
  const presentIds = new Set(trades.map((t) => t.id));
  const toRow = (t: Trade): TradeRow => {
    const paired = t.paired_leg;
    if (paired && !presentIds.has(paired.id)) {
      return { kind: "pair", main: t, sub: paired, derived: true };
    }
    return { kind: "single", trade: t };
  };

  const rows: TradeRow[] = [];
  for (const [key, legs] of groups) {
    if (key.startsWith("sub_")) {
      // 申赎腿恒 single：对手方是 subscription 记录、不是 trade 行（#719 刻意边界，
      // 后端也恒不回填），此处不走 toRow 的派生兜底
      legs.forEach((t) => rows.push({ kind: "single", trade: t }));
      continue;
    }
    if (legs.length === 2) {
      const [a, b] = legs;
      const aIsCash = isCashLeg(a);
      const bIsCash = isCashLeg(b);
      // 恰一条 CASH 才成对；两腿均非 CASH（异常数据）不满足，落规则 5
      if (aIsCash !== bIsCash) {
        rows.push({
          kind: "pair",
          main: aIsCash ? b : a,
          sub: aIsCash ? a : b,
          derived: false,
        });
        continue;
      }
      if (aIsCash && bIsCash) {
        const sell = a.trade_type === "sell" ? a : b.trade_type === "sell" ? b : null;
        const buy = sell === a ? b : sell === b ? a : null;
        // 缺 sell（均 buy）或另一腿非 buy（均 sell）均属异常数据，落规则 5
        if (sell && buy && buy.trade_type === "buy") {
          rows.push({ kind: "pair", main: sell, sub: buy, derived: false });
          continue;
        }
      }
    }
    legs.forEach((t) => rows.push(toRow(t)));
  }
  return rows;
}

/**
 * 配对现金腿是否**已到账**（#493 评审加固；见[现金账本](../../../docs/reference/business-constraints.md#rule-cash)）：
 * 须 **confirmed 且生效日不在未来**——只看日期会把「已到期但尚未确认」的腿
 * （跨天现金转移的转入腿、卖出到账腿）显示成已到账，而 pending 腿**不计入可用现金**。
 * 无生效日（尚未生效的 pending 腿）按未到账处理；cancelled 腿一律未到账。
 *
 * 买入方向刻意**不消费本判据**（`cashSubMeta` 直接丢弃 `arrived`）：扣款腿创建即
 * confirmed、现金日 = 下单日 T，与下单同日完成。未来 T 的预约/补录虽可能存在
 * （`create_trade` 只校验交易日与晚于最新快照日，不禁止未来日），但「扣款已记账」
 * 是既成事实，与卖出「份额已扣、资金未到账」的在途语义不同源——故买入恒显示
 * 「现金扣款」，不设「现金待扣款」态。
 */
export function cashLegArrived(sub: SubLeg, today: string = toDateOnly(new Date())): boolean {
  if (!sub.confirm_date) return false;
  return sub.status === "confirmed" && sub.confirm_date <= today;
}

/**
 * 现金子行派生数据（规范 §8）：主行为买入 → 现金扣款（-）；主行为卖出 → 现金到账（+）。
 * 符号为语义修饰，展示层手工前缀，不回写数值、不走涨跌色 token。
 *
 * `arrived=false`（#493）：配对现金腿尚未实际到账——判据见 `cashLegArrived`
 * （**confirmed 且日期不在未来**，不是只看日期：卖出到账腿可携带未来
 * `confirm_date`，跨天转移的转入腿到期未确认时仍 pending）。主行状态讲的是基金腿，
 * 故在此显式区分——**未来到账的 confirmed 现金、以及已到期仍 pending 的现金，
 * 都不得标成已经到账**。
 * 缺省 true = 沿用旧行为（买入扣款腿创建即 confirmed、现金日 = 下单日 T）。
 * 买入侧的 `arrived` 被刻意忽略，依据见 `cashLegArrived`。
 */
export function cashSubMeta(
  main: Trade,
  options?: { arrived?: boolean }
): { label: "现金扣款" | "现金到账" | "现金待到账"; sign: "-" | "+" } {
  if (main.trade_type === "buy") return { label: "现金扣款", sign: "-" };
  return options?.arrived === false
    ? { label: "现金待到账", sign: "+" }
    : { label: "现金到账", sign: "+" };
}

/**
 * CASH 腿的来源分类（`transfer_group` 编码法，见 trade 模型注释与 §5.8）：
 * `sub_{id}` → 申赎确认；12 位 hex → 平台间转移组；其余（`rebal_{uuid}`）→ 调仓。
 *
 * 这里是该分类口径的**唯一**实现：列表的来源标注与调仓生命周期门控都从它派生，
 * 不再各写一份前缀/正则（#527——两份拷贝曾自述「同源」却互不约束）。
 */
export type CashLegOrigin = "subscription" | "transfer" | "rebalance";

export function cashLegOrigin(trade: Trade): CashLegOrigin {
  const g = trade.transfer_group ?? "";
  if (g.startsWith("sub_")) return "subscription";
  if (/^[0-9a-f]{12}$/.test(g)) return "transfer";
  return "rebalance";
}

/**
 * CASH 孤儿单行的来源标注（§5.8 简化口径：由 transfer_group 前缀推导，不查 subscription、不新增接口）。
 * 用于列表主行「产品」列——那里要的是**业务来源**（这笔现金属于哪类操作）。
 */
export function cashOrphanLabel(trade: Trade): string {
  const origin = cashLegOrigin(trade);
  if (origin === "subscription") return "现金 · 申赎确认";
  if (origin === "transfer") return "现金 · 平台间转移";
  return "现金 · 调仓";
}

const TRADE_TYPE_LABELS: Record<string, string> = { buy: "买入", sell: "卖出" };

/**
 * 现金腿自身的**资金流向**标注（#719，详情页交易卡用）：现金 · 调仓 这类来源标签
 * 说不清「钱动了没有、往哪动」，卡片这一列要的正是后者，故与 `cashOrphanLabel`
 * 并存而不是改写它（列表主行维持 §5.8 来源口径，见 issue #719 待实现改动 10）。
 *
 * 方向由本行 trade_type 直接给出，不查对手腿：调仓/转移组内两腿方向必相反且
 * 现金腿语义固定（扣款=卖出现金、到账=买入现金，`attach_paired_cash_leg`），
 * 因此即使对手腿不在结果集里也读得出来。
 *
 * 「现金到账」仍要过 `cashLegArrived`（#493 口径：confirmed 且生效日不在未来才算已到账）——
 * 卖出到账腿可以携带未来 `confirm_date`，只看日期会把在途现金标成已到账。
 * 转移的转入腿同样走这条判据（#721 评审 S3）：跨天转移的转入腿是 **pending + 未来
 * `confirm_date`**（`cash_transfer_service` 转出腿创建即 confirmed、转入腿等到账日确认），
 * 列表子行按 #493 显示「现金待到账」，卡片若恒写「现金转入」就是同一笔钱两套口径。
 * 转出腿不判：扣款腿创建即 confirmed、转移日 = 资金离开日，与买入扣款腿同源（无「待转出」态）。
 */
export function cashRowLabel(trade: Trade, platformName?: string | null): string {
  const origin = cashLegOrigin(trade);
  const isSell = trade.trade_type === "sell";
  let label: string;
  if (origin === "subscription") {
    label = "现金 · 申赎确认";
  } else if (origin === "transfer") {
    label = isSell ? "现金转出" : cashLegArrived(trade) ? "现金转入" : "现金待到账";
  } else {
    label = isSell ? "现金扣款" : cashLegArrived(trade) ? "现金到账" : "现金待到账";
  }
  return platformName ? `${label} · ${platformName}` : label;
}

/**
 * 对手腿一行文字（#719）：交易页派生子行首列与三处详情页交易卡副行共用（#719）。
 * 现金腿的对手是另一条现金腿（平台间转移）时只说**平台**，对手是基金腿时说是
 * 哪只产品的哪个方向、外加**对手自己的状态**——主行状态列讲的是本行，
 * 半确认组里「基金腿待确认 / 扣款已完成」这种分歧只有在这里才读得出来。
 *
 * 现金对手腿也带状态，但只在**尚未到账**时补「（现金待到账）」（#721 评审 S3）：
 * 跨天转移的转入腿是 pending + 未来生效日，而它挂在已 confirmed 的转出腿下面，
 * 状态列讲的是转出腿——只写「↳ 转入 平台B」会把在途的钱读成已到账。已到账时不加
 * 后缀（`（已确认）` 与主行状态列重复，是噪音），保持规范 §8 承诺的 `↳ 转入 平台名`。
 *
 * 返回 null = 没有可说的对手（转移腿但两侧平台名都取不到），调用方据此不渲子行，
 * 而不是退化成 `--`（宁可少一行信息，不造一个读不懂的占位）。
 */
export function counterpartSubLabel(
  sub: SubLeg,
  counterpartPlatformName?: string | null
): string | null {
  if (isCashLeg(sub)) {
    const name = counterpartPlatformName ?? sub.platform_code;
    if (!name) return null;
    if (sub.trade_type !== "buy") return `↳ 转出 ${name}`;
    return cashLegArrived(sub) ? `↳ 转入 ${name}` : `↳ 转入 ${name}（现金待到账）`;
  }
  const shown =
    sub.product_name ?? formatProductCodeLabel(sub.display_code, sub.product_code, sub.market);
  return `↳ ${TRADE_TYPE_LABELS[sub.trade_type] ?? sub.trade_type} ${shown}（${getTradeStatusLabel(sub.status)}）`;
}

/**
 * 对手腿生效日文案（#719）：卖出腿的 `confirm_date` 是**钱到账日**，前缀「到账」——
 * 这正是筛选现金后用户最想知道的一件事（#719 背景）；买入腿的是份额确认日，
 * 与主行日期列同义，不加前缀。无生效日回空串：子行是补充信息，空比假值好。
 */
export function counterpartDateText(sub: SubLeg): string {
  if (!sub.confirm_date) return "";
  return `${sub.trade_type === "sell" ? "到账 " : ""}${formatDate(sub.confirm_date)}`;
}

/**
 * 详情页交易卡副行文案（#719，三处同形卡片共用一份实现）。
 *
 * 现金腿**没有份额/净值语义**（`shares=None`、`price=1`，见 `attach_paired_cash_leg`），
 * 而三处卡片的旧判据写成 `trade.shares !== undefined`——对 `null` 为真，于是渲成
 * `-- @ 1.0000`：虚假的 1.0000 净值暗示「净值型产品」，对现金是误导。这里改用 `!= null`
 * 并给现金行换成对手腿文案（「从哪来、到哪去」）。判据只留一份，避免三个拷贝再次各自漂移。
 *
 * 日期用**本行**（现金腿）的生效日：扣款腿=扣款日 T、到账腿=到账日 A，与副行原来
 * 放交易日的位置语义一致；对手腿自己的日期在列表子行（`counterpartDateText`）里读。
 * 没有对手腿可说时（`sub_` 申赎腿、尚未建出现金腿的待确认卖出）只回日期，不编造文案。
 */
export function tradeCardDetailLine(
  trade: Trade,
  platformNameMap?: Map<string, string>
): string {
  if (!isCashLeg(trade)) {
    const dateText = formatDate(trade.trade_date);
    if (trade.shares == null) return dateText;
    return `${formatSharesUnit(trade.shares)} @ ${formatNav(trade.price)} · ${dateText}`;
  }
  const sub = trade.paired_leg;
  const label = sub
    ? counterpartSubLabel(sub, sub.platform_code ? platformNameMap?.get(sub.platform_code) : undefined)
    : null;
  const dateText = formatDate(trade.confirm_date ?? trade.trade_date);
  return label ? `${label} · ${dateText}` : dateText;
}
