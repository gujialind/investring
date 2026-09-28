import { request } from "./client";
import {
  Portfolio,
  PortfolioCreate,
  PortfolioUpdate,
  PortfolioValueSnapshot,
  NavHistoryRecord,
  PortfolioReturns,
  PortfolioPerformance,
} from "@/types/portfolio";
import { Position, PositionCreate, PositionUpdate } from "@/types/position";
import { HoldingsByProductResponse, HoldingsByPlatformResponse } from "@/types/holding";
import { PaginatedResponse } from "@/types/common";

// #595 §4.5：现金市值覆盖响应类型
export interface CashPositionUpdateResponse {
  success: boolean;
  message: string;
  portfolio_code: string;
  platform_code: string;
  cash_amount: number;
  computed_value: number | null;
  update_date: string;
  requires_snapshot_regen: boolean;
  warnings: string[];
}

export interface CashOverrideItem {
  id: number;
  platform_code: string;
  platform_name: string;
  update_date: string;
  manual_value: number;
  computed_value: number | null;
  created_by: string | null;
  created_at: string;
}

export interface CashOverrideListResponse {
  items: CashOverrideItem[];
  total: number;
}

export const portfolioApi = {
  list: (params?: { page?: number; page_size?: number; status?: string }) =>
    request<PaginatedResponse<Portfolio>>({ method: "GET", url: "/portfolios", params }),

  get: (code: string) =>
    request<Portfolio>({ method: "GET", url: `/portfolios/${code}` }),

  create: (data: PortfolioCreate) =>
    request<Portfolio>({ method: "POST", url: "/portfolios", data }),

  update: (code: string, data: PortfolioUpdate) =>
    request<Portfolio>({ method: "PUT", url: `/portfolios/${code}`, data }),

  close: (code: string) =>
    request<Portfolio>({ method: "POST", url: `/portfolios/${code}/close` }),

  activate: (code: string) =>
    request<Portfolio>({ method: "POST", url: `/portfolios/${code}/reactivate` }),

  // 注：后端无 DELETE /portfolios/{code} 端点（实体删除均为 RESTRICT，用关闭代替删除）

  getLatestSnapshot: (code: string) =>
    request<PortfolioValueSnapshot>({ method: "GET", url: `/portfolios/${code}/snapshots/latest` }),

  getNavHistory: (code: string, params?: { start_date?: string; end_date?: string }) =>
    request<NavHistoryRecord[]>({ method: "GET", url: `/portfolios/${code}/nav-history`, params }),

  getReturns: (code: string) =>
    request<PortfolioReturns>({ method: "GET", url: `/portfolios/${code}/returns` }),

  // 全量绩效指标（TWR / MWR / 区间收益 / 回撤 / 波动率）
  getPerformance: (code: string) =>
    request<PortfolioPerformance>({ method: "GET", url: `/portfolios/${code}/performance` }),

  getAvailableCash: (code: string) =>
    request<{ available_cash: number }>({ method: "GET", url: `/positions/portfolio/${code}/available-cash` }),

  getInvestors: (code: string) =>
    request<{ investor_code: string; name: string; shares: number }[]>({
      method: "GET",
      url: `/portfolios/${code}/investors`,
    }),
};

// 持仓管理 API（与组合强相关，归入此模块）
export const positionApi = {
  list: (portfolioCode: string, params?: { page?: number; page_size?: number; snapshot_date?: string }) =>
    request<PaginatedResponse<Position>>({
      method: "GET",
      url: `/positions`,
      params: { portfolio_code: portfolioCode, ...params },
    }),

  create: (data: PositionCreate) =>
    request<Position>({ method: "POST", url: "/positions", data }),

  update: (id: number, data: PositionUpdate) =>
    request<Position>({ method: "PUT", url: `/positions/${id}`, data }),

  // 产品可用份额（卖出口径，issue #67）
  getAvailableShares: (portfolioCode: string, productCode: string, market?: string) =>
    request<{ portfolio_code: string; product_code: string; market?: string; available_shares: number }>({
      method: "GET",
      url: `/positions/portfolio/${portfolioCode}/product/${productCode}/available-shares`,
      params: market ? { market } : undefined,
    }),

  // 投资人可用份额（赎回口径，issue #67）
  getInvestorAvailableShares: (portfolioCode: string, investorCode: string) =>
    request<{ portfolio_code: string; investor_code: string; available_shares: number }>({
      method: "GET",
      url: `/positions/portfolio/${portfolioCode}/investor/${investorCode}/available-shares`,
    }),

  // #595 §4.5：现金市值覆盖（写 manual_market_value，绝对替换）
  updateCashPosition: (portfolioCode: string, amount: number, platformCode: string, updateDate?: string) =>
    request<CashPositionUpdateResponse>({
      method: "POST",
      url: `/positions/portfolio/${portfolioCode}/cash-position`,
      data: { cash_amount: amount, platform_code: platformCode, update_date: updateDate },
    }),

  // #595 §4.5：查询现金手动覆盖记录
  listCashOverrides: (portfolioCode: string, params?: { platform_code?: string; start_date?: string; end_date?: string }) =>
    request<CashOverrideListResponse>({
      method: "GET",
      url: `/positions/portfolio/${portfolioCode}/cash-position`,
      params,
    }),

  // #595 §4.5：撤销现金手动覆盖（回退自然值）
  deleteCashOverride: (portfolioCode: string, platformCode: string, updateDate: string) =>
    request<{ success: boolean; message: string }>({
      method: "DELETE",
      url: `/positions/portfolio/${portfolioCode}/cash-position`,
      params: { platform_code: platformCode, update_date: updateDate },
    }),

  // #595 组合详情页双视图：按产品 / 按平台聚合
  getHoldingsByProduct: (portfolioCode: string) =>
    request<HoldingsByProductResponse>({
      method: "GET",
      url: `/positions/portfolio/${portfolioCode}/holdings/by-product`,
    }),

  getHoldingsByPlatform: (portfolioCode: string) =>
    request<HoldingsByPlatformResponse>({
      method: "GET",
      url: `/positions/portfolio/${portfolioCode}/holdings/by-platform`,
    }),
};
