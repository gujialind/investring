export interface Trade {
  id: number;
  portfolio_code: string;
  product_code: string;
  market?: string;
  platform_code?: string;
  trade_type: string;
  shares?: number;
  amount?: number;
  price?: number;
  fee: number;
  actual_amount?: number;
  trade_date: string;
  confirm_date?: string;
  status: string;
  /** 业务分组（#126 决策⑨）：rebal_*=调仓配对、sub_*=申赎现金腿、12位hex=现金转移；仅用于结对展示，页面不展示该编码 */
  transfer_group?: string;
  notes?: string;
  created_at?: string;
  updated_at?: string;
  /** 读侧派生（#175）：仅 list 响应有值；create/get/update 恒为 undefined */
  product_name?: string;
  /**
   * 读侧派生现金信息（#493）：**只读、不落库、仅基金腿填充**——后端按
   * `(portfolio_code, transfer_group)` 批量查反向 CASH 腿，因此不受列表的
   * 状态/平台筛选与分页截断影响，半确认组（买入扣款腿已 confirmed、基金腿
   * pending）同样可见；待确认卖出尚无 CASH 腿 → 两者为 null。
   * CASH 腿自身不回填（恒 null）。列表/详情一律读这两个字段，
   * **不要**再从当前页里寻找配对 CASH 行。
   * 语义：买入=扣款平台/扣款日（=下单日 T）；卖出=到账平台/到账日 A。
   */
  cash_platform_code?: string | null;
  cash_confirm_date?: string | null;
}

export interface TradeCreate {
  portfolio_code: string;
  product_code: string;
  market?: string;
  platform_code?: string;
  /**
   * 跨平台现金腿（#91/#493）：**仅买入可用**=扣款平台（缺省同基金腿平台），
   * 落库为配对 CASH sell 腿并创建即 confirmed。
   * 卖出传它 → 422 CASH_PLATFORM_NOT_ALLOWED（到账平台改在 confirm/preview 录入）。
   */
  cash_platform_code?: string;
  trade_type: string;
  shares?: number;
  amount?: number;
  price?: number;
  fee?: number;
  actual_amount?: number;
  trade_date: string;
  notes?: string;
  /** 命中 DUPLICATE_TRADE 时用户确认后重试传 true（后端默认 false） */
  allow_duplicate?: boolean;
}

// 字段与后端 schemas/trade.py::TradeUpdate 对齐：
// 不含 confirm_date/status（后端会静默丢弃）；改状态请走 confirm/unconfirm/cancel 端点
export interface TradeUpdate {
  shares?: number;
  // amount 语义（#182 D1，与创建同口径）：buy/sell 均为实际金额口径
  // （buy=含费现金支出、sell=到手净额），与 actual_amount 同义、后者优先
  amount?: number;
  price?: number;
  fee?: number;
  actual_amount?: number;
  trade_date?: string;
  notes?: string;
  /**
   * 卖出到账日修正（#493）：**已确认卖出的窄例外**——只允许
   * `{cash_confirm_date, notes?}` 子集，混入任何其他字段整体拒绝、显式 null 拒绝；
   * pending 交易传它 → 422 INVALID_PARAM（pending 走 trade_date 重算）。
   */
  cash_confirm_date?: string;
}

// 确认前预览（#248）：与真实确认共用后端计算实现
export interface TradePreviewResult {
  price?: number;
  shares?: number;
  amount?: number;
  actual_amount?: number;
  fee: number;
  confirm_date?: string;
  nav_date?: string;
  is_otc_nav_fund: boolean;
  /** 本次有效扣款/到账平台（#493）：买=扣款平台（创建期已定）、卖=传入值或基金腿平台 */
  cash_platform_code?: string | null;
  /** 本次有效现金日（#493）：买=下单日 T、卖=到账日 A（缺省 = 本次有效确认日 C） */
  cash_confirm_date?: string | null;
}

export interface TradePreviewResponse {
  trade: Trade;
  preview: TradePreviewResult;
  paired_cash_amount?: number;
}

/**
 * 确认/preview 的业务输入（#493）：两者同一组参数，**一律走 query 参数**
 * （后端 confirm/preview 端点均为 query 协议，JSON body 会被静默忽略）。
 * - `confirm_date`：有效基金确认日 C（缺省取创建时按 confirm_days 推导的值）
 * - `price`：场内成交价覆盖；场外仅作与 T 日净值的一致性校验
 * - `cash_confirm_date`：卖出到账日 A（缺省 = C；买入只接受等于 T）
 * - `cash_platform_code`：卖出到账平台（缺省 = 基金腿平台；买入不可改扣款平台）
 */
export interface TradeConfirmParams {
  confirm_date?: string;
  price?: number;
  cash_confirm_date?: string;
  cash_platform_code?: string;
  /** 仅 confirm 端点：命中 MISSING_NAV 时显式同步净值后重试（preview 不产生写入） */
  sync_nav?: boolean;
}

/**
 * 确认端点的外层响应结构（#493 §3.3：保持现状，新增信息进入其中的 `trade`）：
 * 顶层平铺字段是历史契约（CLI 也在读），**交易本体在 `trade` 字段里**。
 */
export interface TradeConfirmResponse {
  message: string;
  id: number;
  portfolio_code: string;
  trade_type: string;
  status: string;
  confirm_date?: string | null;
  trade: Trade;
}
