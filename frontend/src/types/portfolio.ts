export interface Portfolio {
  code: string;
  name: string;
  description?: string;
  status: string;
  /** 组合级自动快照开关（issue #156）：开启后由定时任务每个交易日自动补齐快照 */
  auto_snapshot_enabled: boolean;
  started_at?: string;
  closed_at?: string;
  created_at?: string;
  updated_at?: string;
  /** 持仓明细二级分组维度覆盖（issue #144）：{"ASSET_STOCK": "style"}；
   * null/缺省 = 前端内置默认；仅存显式覆盖项 */
  display_config?: Record<string, string> | null;
  // 以下字段由后端在列表接口中动态计算返回
  total_value?: number;
  cumulative_return?: number;
  investor_count?: number;
  // 详情接口派生字段（issue #99）：最新快照总资产 / 累计收益（总资产 − 净投入）
  // 无快照（draft 等）时为 null
  total_profit?: number | null;
}

export interface PortfolioCreate {
  code: string;
  name: string;
  description?: string;
  display_config?: Record<string, string> | null;
}

export interface PortfolioUpdate {
  name?: string;
  description?: string;
  /** 显式传 null = 清空配置恢复默认；不传 = 不修改 */
  display_config?: Record<string, string> | null;
  /** 不传 = 不修改（#156） */
  auto_snapshot_enabled?: boolean;
}

export interface PortfolioValueSnapshot {
  id: number;
  portfolio_code: string;
  total_value: number;
  total_shares: number;
  unit_price: number;
  snapshot_date: string;
  created_at?: string;
}

/**
 * GET /portfolios/{code}/investors 返回项（#693）。
 *
 * 派生列来自 investor_holding 快照落库值；#40 之前生成且从未重算的历史快照
 * 可为 null（界面显示 `--`，不兜底成 0）。口径对齐后端 PortfolioInvestorItem：
 * ratio 为占组合总份额比（0-1 小数）、profit_percent 为百分数（×100）。
 */
export interface PortfolioInvestorItem {
  investor_code: string;
  name: string;
  shares: number;
  frozen_shares: number | null;
  cost_per_share: number | null;
  market_value: number | null;
  total_cost: number | null;
  /** 持仓收益 = market_value − total_cost（当前在手份额浮动盈亏，与 #598「累计收益」口径不同） */
  profit: number | null;
  /** profit / total_cost × 100；total_cost 为 null/0 时 null */
  profit_percent: number | null;
  /** shares / 当日市值快照 total_shares；无当日市值快照行时 null */
  ratio: number | null;
  /** 累计收益（#694 方案 A：D 日市值 + Σconfirmed 赎回 − Σconfirmed 申购）；
   * 缺省路径无市值快照时 null；与 profit「持仓收益」口径不同，界面带 * 注记 */
  cumulative_profit: number | null;
  /** 清仓行标记：当日无持仓行、份额/市值计 0、保留累计收益 */
  is_liquidated: boolean;
}

// GET /portfolios/{code}/nav-history 返回项
export interface NavHistoryRecord {
  snapshot_date: string;
  unit_price: number | null;
  total_value: number | null;
  total_shares: number | null;
}

// GET /portfolios/{code}/returns 返回（轻量口径：累计 + 年化）
export interface PortfolioReturns {
  portfolio_code: string;
  cumulative_return: number | null;
  annualized_return: number | null;
  initial_nav: number | null;
  current_nav: number | null;
  holding_days: number | null;
}

/**
 * GET /portfolios/{code}/performance 返回（全量绩效与风险指标）
 *
 * twr：时间加权收益率，消除资金进出影响（本系统为净值化记账，等于净值增长率）
 * mwr：资金加权收益率（XIRR），反映实际投入资金的年化回报
 * twr > mwr 通常说明大部分资金买在高位（加仓时点不佳）
 */
export interface PortfolioPerformance {
  portfolio_code: string;
  twr: number | null;
  twr_chained: number | null;
  annualized_twr: number | null;
  mwr: number | null;
  initial_nav: number | null;
  current_nav: number | null;
  holding_days: number | null;
  return_1m: number | null;
  return_3m: number | null;
  return_6m: number | null;
  return_ytd: number | null;
  return_1y: number | null;
  return_3y: number | null;
  max_drawdown: number | null;
  max_drawdown_peak_date: string | null;
  max_drawdown_trough_date: string | null;
  annualized_volatility: number | null;
  cash_flow_count: number;
  /** 两种 TWR 算法一致性自检：false 说明净值序列异常 */
  nav_series_consistent: boolean | null;
  /** 持有期 < 90 天时为 false：年化（尤其 MWR）属大幅外推，仅供参考 */
  annualization_reliable: boolean;
}
