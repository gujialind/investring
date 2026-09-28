import { request, ApiException } from "./client";
import { Product, ProductCreate, ProductUpdate } from "@/types/product";
import { PaginatedResponse } from "@/types/common";

export interface PriceDataPoint {
  product_code: string;
  market: string;
  price_date: string;
  unit_price: number;
}

// --- #595 §5.3 产品详情页净值数据（步骤③-1 后端端点的前端契约） ---

/** 历史净值行：单位净值必有；累计净值/日涨跌可空（场内 ETF 行情源常无累计净值）。 */
export interface NavHistoryItem {
  price_date: string;
  unit_price: number;
  accumulated_nav: number | null;
  pct_change: number | null;
}

export interface NavHistoryPage {
  items: NavHistoryItem[];
  total: number;
  /** 信封回显请求参数（#637 L2 评审 S1，与全仓分页信封对齐） */
  page: number;
  page_size: number;
}

/** 六窗区间收益率（百分数 4dp；历史不足窗口期为 null，前端显示占位）。 */
export interface ProductIntervalReturns {
  m1: number | null;
  m3: number | null;
  m6: number | null;
  y1: number | null;
  ytd: number | null;
  all: number | null;
}

export interface NavCurvePoint {
  date: string;
  accumulated_nav: number;
}

export interface ProductNavAnalysis {
  curve: NavCurvePoint[];
  interval_returns: ProductIntervalReturns;
}

/** 净值曲线区间（与后端 nav-analysis 的 range 参数同码） */
export type NavAnalysisRange = "1m" | "3m" | "6m" | "1y";

/**
 * market 是后端必填路径参数：缺失时抛 ApiException 而不是拼出 `/products/CODE/` 这类
 * 带空段的畸形 URL（会得到令人困惑的 307/404/405）。
 */
function requireMarket(market: string | undefined, action: string): string {
  if (!market) {
    throw new ApiException(
      "MARKET_REQUIRED",
      `${action}需指定市场（market），现金类产品不支持此操作`,
      422
    );
  }
  return market;
}

/**
 * 产品列表查询参数。维度筛选参数（issue #128）：asset_class/region/style/size/segment 五维 code；
 * keyword（issue #155）：code/name 模糊匹配，与其余参数 AND 叠加；
 * confirm_days/nav_lag_days/is_qdii（issue #238）：属性等值筛选（0/false 为合法值，
 * axios 只丢 undefined，显式传 0/false 正常序列化）。
 */
export interface ProductListParams {
  page?: number;
  page_size?: number;
  product_type?: string;
  market?: string;
  data_source?: string;
  keyword?: string;
  confirm_days?: number;
  nav_lag_days?: number;
  is_qdii?: boolean;
  asset_class_code?: string;
  region_code?: string;
  style_code?: string;
  size_code?: string;
  segment_code?: string;
  /** issue #327：后端默认排除虚拟产品（product_type 为 CASH/IN_TRANSIT），管理页传 true 展示全部 */
  include_virtual?: boolean;
}

export const productApi = {
  list: (params?: ProductListParams) =>
    request<PaginatedResponse<Product>>({ method: "GET", url: "/products", params }),

  // 后端两个端点：/products/{code}/{market} 精确匹配；/products/{code} 自动解析（一码多市场时抛 MARKET_AMBIGUOUS）。
  // market 不能用 query 传（后端不接收，会被忽略）
  get: (code: string, market?: string) =>
    request<Product>({ method: "GET", url: market ? `/products/${code}/${market}` : `/products/${code}` }),

  create: (data: ProductCreate) =>
    request<Product>({ method: "POST", url: "/products", data }),

  // 以下端点 market 为必填路径参数，缺失时尽早报错而不是发出尾部带空段的畸形 URL
  update: (code: string, data: ProductUpdate, market?: string) =>
    request<Product>({ method: "PUT", url: `/products/${code}/${requireMarket(market, "更新产品")}`, data }),

  delete: (code: string, market?: string) =>
    request<{ message: string }>({ method: "DELETE", url: `/products/${code}/${requireMarket(market, "删除产品")}` }),

  syncPrice: (code: string, market?: string, data?: { start_date?: string; end_date?: string }) =>
    request<{ message: string; synced_count?: number }>({
      method: "POST",
      url: `/market-data/products/${code}/${requireMarket(market, "同步价格")}/sync-price-data`,
      data,
    }),

  syncHistory: (code: string, market?: string) =>
    request<{ message: string; synced_count?: number }>({
      method: "POST",
      url: `/market-data/products/${code}/${requireMarket(market, "同步历史价格")}/sync-history`,
    }),

  getPriceData: (code: string, market: string, params?: { start_date?: string; end_date?: string; limit?: number }) =>
    request<PriceDataPoint[]>({
      method: "GET",
      url: `/market-data/products/${code}/${market}/price-data`,
      params,
    }),

  // #595 §5.3 产品详情页：历史净值分页（日期降序，首屏 5 行 + 查看更多）
  getNavHistory: (
    code: string,
    market: string,
    params?: { page?: number; page_size?: number; start_date?: string; end_date?: string }
  ) =>
    request<NavHistoryPage>({
      method: "GET",
      url: `/market-data/products/${code}/${market}/nav-history`,
      params,
    }),

  // #595 §5.3 产品详情页：区间累计净值曲线 + 六窗区间收益率
  getNavAnalysis: (code: string, market: string, range: NavAnalysisRange) =>
    request<ProductNavAnalysis>({
      method: "GET",
      url: `/market-data/products/${code}/${market}/nav-analysis`,
      params: { range },
    }),
};
