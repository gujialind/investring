import { request } from "./client";
import {
  CashFlowStatusFilter,
  CashFlowType,
  PaginatedCashFlowResponse,
} from "@/types/cash-flow";

/**
 * 现金流水列表查询参数（issue #725）。
 * axios 丢弃 undefined 值，空筛选自然不传参；上界 to_date 缺省开放，否则预计
 * 到账日在未来的在途行会被默认视图藏掉。
 */
export interface CashFlowListParams {
  from_date?: string;
  to_date?: string;
  status?: CashFlowStatusFilter;
  platform_code?: string;
  flow_type?: CashFlowType;
  page?: number;
  page_size?: number;
}

export const cashFlowApi = {
  /**
   * 全来源现金流水（调仓/申赎 CASH 腿、平台间转移、平台级现金事件、手动重估），
   * 含页头概览四卡与按平台构成。端点复数 `cash-flows`，与既有申赎汇总
   * `cash-flow`（单数）口径不同，见 backend/app/routers/cash_flows.py。
   */
  list: (portfolioCode: string, params?: CashFlowListParams) =>
    request<PaginatedCashFlowResponse>({
      method: "GET",
      url: `/portfolios/${portfolioCode}/cash-flows`,
      params,
    }),
};
