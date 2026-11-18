// 平台市值台账型组合（issue #905 PR3）数据层类型。
// 与后端 backend/app/schemas/ledger.py 逐键对齐；业务口径见
// docs/reference/business-constraints.md#rule-ledger-portfolio。
// 台账型 kind='ledger'：按平台记绝对市值（每平台每日一行，可改可删）+ 本金进出（同日多笔），
// 收益 = 当前市值 − 净入金（step-forward），不产生净值/份额/快照。

/** 市值台账行（ledger_entry）：每平台每日一行绝对市值 */
export interface LedgerEntry {
  id: number;
  portfolio_code: string;
  platform_code: string;
  value_date: string;
  market_value: number;
  /** 展示分类标签：cash / non_cash（隔离不依赖此标签，依赖独立表不被现金口径消费） */
  asset_kind: string;
  note?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LedgerEntryCreate {
  platform_code: string;
  value_date: string;
  market_value: number;
  asset_kind?: string;
  note?: string | null;
}

/** D5：可改某日市值/分类/备注；platform_code/value_date 为唯一键组成不改（改日期请删后重建） */
export interface LedgerEntryUpdate {
  market_value?: number;
  asset_kind?: string;
  note?: string | null;
}

/** 本金进出（ledger_flow）：同平台同日可多笔，无唯一约束 */
export interface LedgerFlow {
  id: number;
  portfolio_code: string;
  platform_code: string;
  flow_date: string;
  /** inflow / outflow */
  direction: string;
  amount: number;
  note?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LedgerFlowCreate {
  platform_code: string;
  flow_date: string;
  direction: string;
  amount: number;
  note?: string | null;
}

export interface LedgerFlowUpdate {
  flow_date?: string;
  direction?: string;
  amount?: number;
  note?: string | null;
}

/** 进出写入响应附告警（S4：净入金为负时提示，不硬拒不静默） */
export interface LedgerFlowWriteResult extends LedgerFlow {
  warnings: string[];
}

/** 收益汇总（ledger/totals）：draft → 各值 null；active 无记录 → 0 */
export interface LedgerTotals {
  portfolio_code: string;
  as_of_date?: string | null;
  total_value?: number | null;
  net_inflow?: number | null;
  total_profit?: number | null;
  warnings: string[];
}