/**
 * #595 组合详情页双视图：按产品 / 按平台聚合响应类型
 * （对齐 backend/app/schemas/position.py 的 Holdings* 响应模型）。
 */

export interface HoldingProductPlatformSlice {
  platform_code: string | null;
  platform_name: string | null;
  market_value: number;
  shares: number | null;
  cash_amount: number | null;
  holding_profit: number | null;
  cumulative_profit: number | null;
  ratio_in_product: number | null;
}

export interface HoldingProductAggregate {
  product_code: string;
  market: string;
  product_name: string | null;
  // 五维度标签（前端大类/维度分组元数据）
  asset_class_code: string | null;
  asset_class_name: string | null;
  region_code: string | null;
  region_name: string | null;
  style_code: string | null;
  style_name: string | null;
  size_code: string | null;
  size_name: string | null;
  segment_code: string | null;
  segment_name: string | null;
  market_value: number;
  /** 占组合总市值比（含在途基数），0-1 小数 */
  ratio: number | null;
  shares: number | null;
  cash_amount: number | null;
  /** 持有收益（卡片「累计收益」标签口径，D-3） */
  holding_profit: number | null;
  holding_profit_percent: number | null;
  daily_profit: number | null;
  /** 累计收益（含已实现，#598 口径）；无市值快照时 null（前端可空占位） */
  cumulative_profit: number | null;
  platforms: HoldingProductPlatformSlice[];
}

export interface HoldingsByProductResponse {
  portfolio_code: string;
  snapshot_date: string | null;
  /** 含在途（在途计市值） */
  total_market_value: number;
  /** 在途虚拟产品市值合计（#595 评审决策补回在途聚合卡；0 = 无在途） */
  in_transit_market_value: number;
  products: HoldingProductAggregate[];
}

export interface HoldingPlatformAggregate {
  platform_code: string | null;
  platform_name: string | null;
  platform_type: string | null;
  /** 含该平台现金与在途 */
  market_value: number;
  cash_balance: number;
  /** 非现金、非在途持仓产品数 */
  product_count: number;
  holding_profit: number | null;
  cumulative_profit: number | null;
  ratio: number | null;
}

export interface HoldingsByPlatformResponse {
  portfolio_code: string;
  snapshot_date: string | null;
  total_market_value: number;
  platforms: HoldingPlatformAggregate[];
}

/** 持仓明细视图切换（D-7：URL 参数 ?view=product|platform，默认 product） */
export type HoldingsView = "product" | "platform";

export function parseHoldingsView(raw: string | null): HoldingsView {
  return raw === "platform" ? "platform" : "product";
}
