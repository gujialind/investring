import { EventType, TransactionStatus } from "./common";

// 字段与后端 schemas/share_change_event.py 对齐
export interface ShareChangeEvent {
  id: number;
  portfolio_code: string;
  event_type: EventType;
  ex_date: string;
  entitlement_date: string;
  cash_pay_date?: string | null;
  /** 再投资份额确认日/份额到账日（#743）：仅 reinvest_dividend 可设，须为交易日且晚于除息日 */
  share_confirm_date?: string | null;
  platform_code?: string;
  parent_event_id?: number;
  product_code?: string;
  market?: string;
  /** 读侧派生（非 DB 列）：仅 list 端点填充，单对象端点恒为 undefined（#342） */
  product_name?: string;
  /** 读侧派生显示码（#705）：填充面同 product_name；标识符用途仍用 product_code */
  display_code?: string | null;
  event_source?: string; // manual / tushare
  entitlement_shares?: number;
  shares_before?: number;
  shares_change?: number;
  shares_after?: number;
  cash_change?: number;
  cash_product_code?: string;
  div_cash?: number;
  reinvest_nav?: number;
  ratio?: number;
  status: TransactionStatus;
  tushare_event_id?: string;
  notes?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ShareChangeEventCreate {
  portfolio_code: string;
  event_type: EventType;
  ex_date: string;
  entitlement_date: string;
  cash_pay_date?: string | null;
  /** 再投资份额确认日/份额到账日（#743）：仅 reinvest_dividend 可设，其他类型不得提交 */
  share_confirm_date?: string | null;
  platform_code?: string;
  parent_event_id?: number;
  product_code?: string;
  market?: string;
  entitlement_shares?: number;
  shares_before?: number;
  shares_change?: number;
  shares_after?: number;
  cash_change?: number;
  /** 现金分红落地的现金产品（平台级现金分红场景） */
  cash_product_code?: string;
  div_cash?: number;
  reinvest_nav?: number;
  ratio?: number;
  notes?: string;
}

// 不含 status：状态流转走 confirm/cancel/unconfirm 端点（后端 Update schema 不接受）
export interface ShareChangeEventUpdate {
  ex_date?: string;
  entitlement_date?: string;
  cash_pay_date?: string | null;
  /** 再投资份额确认日（#743）：null = 清空（仅 reinvest_dividend 可设） */
  share_confirm_date?: string | null;
  shares_before?: number;
  shares_change?: number;
  shares_after?: number;
  cash_change?: number;
  div_cash?: number;
  /** 再投资净值：null = 未填，确认时取除息日数据源净值（#744） */
  reinvest_nav?: number | null;
  ratio?: number;
  notes?: string;
}

/** 完整分红候选（#775）：带方式时，适用日期省略/null 均默认除息日，不沿用旧日期。 */
export interface ShareChangeEventConfirmSelection {
  event_type: "cash_dividend" | "reinvest_dividend";
  cash_pay_date?: string | null;
  share_confirm_date?: string | null;
}

/**
 * 事件确认前预览的计算结果（#424，与后端 ShareChangeEventPreviewResult 对齐）。
 * 与确认后落库值逐一相等；强制调整的 shares_after 恒为 null（确认不写回该字段）。
 * 分红明细（#744）：div_cash / dividend_amount 仅分红类有值；reinvest_nav（确认将回写的
 * 解析净值）与 nav_date（取价日 = 除息日）仅分红再投资有值。
 */
export interface ShareChangeEventPreview {
  event_type: string;
  cash_effective_date: string;
  share_effective_date: string;
  entitlement_shares?: number;
  shares_change?: number;
  shares_after?: number;
  cash_change?: number;
  div_cash?: number | null;
  dividend_amount?: number | null;
  reinvest_nav?: number | null;
  nav_date?: string | null;
}

export interface ShareChangeEventPreviewResponse {
  preview: ShareChangeEventPreview;
}
