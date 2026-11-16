import type { PaginatedResponse } from "./common";

export type DividendMethod = "cash_dividend" | "reinvest_dividend";
export type DividendConfigSource = "default" | "explicit";

/** 分红方式配置行（#887，与后端 schemas/dividend_config.py 对齐）。 */
export interface DividendConfig {
  id: number;
  portfolio_code: string;
  product_code: string;
  market: string;
  platform_code: string;
  dividend_method: DividendMethod;
  effective_from: string;
  /** default=迁移存量预选值（仅预选）；explicit=用户显式配置 */
  source: DividendConfigSource;
  created_at?: string | null;
  updated_at?: string | null;
}

/** 配置键 (组合, 产品, 市场, 平台)；market 省略由后端按产品唯一市场补全。 */
export interface DividendConfigKey {
  portfolio_code: string;
  product_code: string;
  platform_code: string;
  market?: string;
}

export interface DividendConfigCreate extends DividendConfigKey {
  dividend_method: DividendMethod;
  effective_from: string;
  /** 确认弹窗沉淀入口标记（S4 方案 B）：true 时后端取 max(effective_from, 业务当日)。 */
  from_confirmation?: boolean;
}

export interface DividendConfigResolveParams extends DividendConfigKey {
  entitlement_date: string;
}

export type DividendConfigListResponse = PaginatedResponse<DividendConfig>;