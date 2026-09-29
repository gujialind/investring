export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiErrorDetail {
  error: string;
  message: string;
  /** 后端 BusinessError 附带的结构化上下文（如 MARKET_AMBIGUOUS 的 available_markets） */
  details?: Record<string, unknown>;
}

/** FastAPI RequestValidationError 的 detail 条目（#643）：loc 末段是字段名 */
export interface ApiValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface ApiError {
  /**
   * 后端存在三种形态：结构化对象、裸字符串（HTTPException(detail="...")）
   * 与校验失败数组（422 RequestValidationError，#643）。
   * 少声明一种就不会有人写解析分支——#643 的文案裸奔正是数组形态没进联合类型。
   */
  detail: ApiErrorDetail | string | ApiValidationError[];
}

export type Role = "admin" | "viewer";
export type PortfolioStatus = "draft" | "active" | "closed";
export type ProductType = "ETF" | "OEF" | "LOF" | "CASH";
export type TradeType = "buy" | "sell";
export type SubscriptionType = "subscribe" | "redeem";
export type TransactionStatus = "pending" | "confirmed" | "cancelled";
export type EventType =
  | "cash_dividend"
  | "reinvest_dividend"
  | "share_split"
  | "share_merge"
  | "bonus_share"
  | "forced_adjustment";
