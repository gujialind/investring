import { EventType, TransactionStatus } from "./common";

// 字段与后端 schemas/share_change_event.py 对齐
export interface ShareChangeEvent {
  id: number;
  portfolio_code: string;
  event_type: EventType;
  ex_date: string;
  entitlement_date: string;
  platform_code?: string;
  parent_event_id?: number;
  product_code?: string;
  market?: string;
  /** 读侧派生（非 DB 列）：仅 list 端点填充，单对象端点恒为 undefined（#342） */
  product_name?: string;
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
  shares_before?: number;
  shares_change?: number;
  shares_after?: number;
  cash_change?: number;
  div_cash?: number;
  reinvest_nav?: number;
  ratio?: number;
  notes?: string;
}

/**
 * 事件确认前预览的计算结果（#424，与后端 ShareChangeEventPreviewResult 对齐）。
 * 与确认后落库值逐一相等；强制调整的 shares_after 恒为 null（确认不写回该字段）。
 */
export interface ShareChangeEventPreview {
  entitlement_shares?: number;
  shares_change?: number;
  shares_after?: number;
  cash_change?: number;
}

export interface ShareChangeEventPreviewResponse {
  preview: ShareChangeEventPreview;
}
