/**
 * 现金流水读侧契约（issue #725）。镜像 backend/app/schemas/cash_flow.py。
 *
 * 金额字段（amount / *_balance / overview.*）后端为 `Decimal` 4 位原值，Pydantic v2
 * 序列化为 **JSON 字符串**直出——前端不做展示量化以外的改写，2 位呈现由
 * `formatCurrency`（lib/utils）承担，纯函数运算经 `toAmount` 收敛为 number。
 */

/** 行状态 variant：success=已生效 / warning=在途 / neutral=非流量（重估） */
export type CashFlowStatusVariant = "success" | "warning" | "neutral";

export interface CashFlowStatus {
  variant: CashFlowStatusVariant;
  label: string;
  note?: string | null;
}

/** 行操作提示 kind：detail=打开详情弹窗 / confirm=确认到账 / revoke=撤销重估 */
export type CashFlowActionKind = "detail" | "confirm" | "revoke";

export interface CashFlowAction {
  label: string;
  kind: CashFlowActionKind;
}

export interface CashFlowOriginRef {
  kind: "trade" | "share_change_event" | "manual_market_value";
  id: number;
  transfer_group?: string | null;
}

export interface CashFlowPairRef {
  kind: string;
  id: number;
  label: string;
  platform_code?: string | null;
  platform_name?: string | null;
  ref_date?: string | null;
  amount?: string | null;
  settled?: boolean | null;
}

/**
 * flow_type 七值（后端 FLOW_TYPE_LABELS 权威）：rebal_buy / rebal_sell / sub_buy /
 * redeem / dividend / transfer / revalue。label 一律取后端 `flow_type_label`，前端不重算方向。
 */
export type CashFlowType =
  | "rebal_buy"
  | "rebal_sell"
  | "sub_buy"
  | "redeem"
  | "dividend"
  | "transfer"
  | "revalue";

export interface CashFlowRow {
  source_kind: string;
  flow_type: CashFlowType;
  flow_type_label: string;
  cash_date: string;
  cash_date_label: string;
  cash_date_sub?: string | null;
  platform_code: string;
  platform_name?: string | null;
  source_main: string;
  source_sub?: string | null;
  /** 带符号金额（流入为正、流出为负）；is_override 行为绝对值，不与增量求和 */
  amount: string;
  is_override: boolean;
  settled: boolean;
  status: CashFlowStatus;
  action: CashFlowAction;
  origin_ref: CashFlowOriginRef;
  pair_ref?: CashFlowPairRef | null;
  /** 同 (平台, 现金日期) 解除类型筛选后的总笔数；「当日另有 N 笔」= 该值 − 在屏同组行数 */
  group_total_count: number;
  platform_end_of_day_balance?: string | null;
  prev_day_balance?: string | null;
}

export interface CashFlowOverview {
  available: string;
  in_transit: string;
  inflow: string;
  outflow: string;
}

export interface PlatformCashFlowSummary {
  platform_code: string;
  platform_name?: string | null;
  available: string;
  in_transit: string;
  inflow: string;
  outflow: string;
}

export interface CashFlowRecon {
  inflow: string;
  outflow: string;
}

export interface PlatformCashFlowBreakdown {
  rows: PlatformCashFlowSummary[];
  total: CashFlowOverview;
  recon: CashFlowRecon;
}

export interface PaginatedCashFlowResponse {
  items: CashFlowRow[];
  total: number;
  page: number;
  page_size: number;
  overview: CashFlowOverview;
  platform_breakdown: PlatformCashFlowBreakdown;
}

/** status 筛选两值（映射派生 settled，不透出 raw trade.status） */
export type CashFlowStatusFilter = "settled" | "in_transit";
