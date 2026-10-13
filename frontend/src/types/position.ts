export interface Position {
  id: number;
  portfolio_code: string;
  product_code: string;
  product_name?: string;
  /** 读侧派生显示码（#705）：标识符用途仍用 product_code */
  display_code?: string | null;
  market?: string;
  platform_code?: string;
  platform_name?: string | null;
  shares?: number;
  frozen_shares?: number;
  cost_price?: number;
  unit_price?: number;
  market_value?: number;
  cash_amount?: number;
  frozen_amount?: number;
  profit_loss?: number;
  profit_loss_percent?: number;
  // 读侧派生（issue #128）：5 维度 code+name 成对输出；CASH 行仅 asset_class=现金、
  // 其余维度 NULL；IN_TRANSIT 在途行五维全 NULL
  asset_class_code?: string | null;
  asset_class_name?: string | null;
  region_code?: string | null;
  region_name?: string | null;
  style_code?: string | null;
  style_name?: string | null;
  size_code?: string | null;
  size_name?: string | null;
  segment_code?: string | null;
  segment_name?: string | null;
  // 读侧派生（issue #99）：daily_profit=当日收益（首个快照日 / IN_TRANSIT 在途行 → null）
  daily_profit?: number | null;
  /** 展示标签，不驱动取价（issue #228 起滞后估值看 nav_lag_days） */
  is_qdii?: boolean | null;
  /** issue #228：快照估值取价滞后交易日数（0=当日；N>0 时前端 tooltip 提示日收益滞后 N 天） */
  nav_lag_days?: number | null;
  snapshot_date: string;
  created_at?: string;
}

export interface PositionCreate {
  portfolio_code: string;
  product_code: string;
  market?: string;
  platform_code?: string;
  shares?: number;
  frozen_shares?: number;
  cost_price?: number;
  unit_price?: number;
  market_value?: number;
  cash_amount?: number;
  frozen_amount?: number;
  snapshot_date: string;
}

export interface PositionUpdate {
  platform_code?: string;
  shares?: number;
  frozen_shares?: number;
  cost_price?: number;
  unit_price?: number;
  market_value?: number;
  cash_amount?: number;
  frozen_amount?: number;
  snapshot_date?: string;
}
