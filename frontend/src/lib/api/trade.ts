import { request } from "./client";
import {
  Trade,
  TradeConfirmParams,
  TradeConfirmResponse,
  TradeCreate,
  TradePreviewResponse,
  TradeUpdate,
} from "@/types/trade";
import { PaginatedResponse } from "@/types/common";

/**
 * 调仓列表查询参数（#126 服务端筛选）。
 * axios 会丢弃 undefined 值，空筛选自然不传参。
 */
export interface TradeListParams {
  page?: number;
  page_size?: number;
  portfolio_code?: string;
  status?: string;
  trade_type?: string;
  product_code?: string;
  market?: string;
  /**
   * 多选产品过滤（issue #155）：逗号分隔的 `code|market` 复合值（market 段可空，如 `CASH|`）。
   * 与 product_code/market 单值参数互斥，同传后端返回 422。
   */
  products?: string;
  platform_code?: string;
  trade_date_start?: string;
  trade_date_end?: string;
  confirm_date_start?: string;
  confirm_date_end?: string;
}

/**
 * cancel / unconfirm / delete 三个写端点的**真实**响应形状（#493 评审加固）：
 * 后端只回 `{message}`，不含交易的任何字段（见 `backend/app/routers/trades.py` 三个
 * 端点均 `return {"message": ...}`）。历史上这里声明为 `Trade`/`void`，修掉读取点后
 * 类型仍在骗人——下一个调用方写 `data.portfolio_code` 依旧 tsc 全绿、运行时 undefined。
 * 组合 code 一律由调用方经 mutation 变量传入。
 */
export interface TradeMessageResponse {
  message: string;
}

export const tradeApi = {
  list: (params?: TradeListParams) =>
    request<PaginatedResponse<Trade>>({ method: "GET", url: "/trades", params }),

  get: (id: number) =>
    request<Trade>({ method: "GET", url: `/trades/${id}` }),

  create: (data: TradeCreate) =>
    request<Trade>({ method: "POST", url: "/trades", data }),

  update: (id: number, data: TradeUpdate) =>
    request<Trade>({ method: "PUT", url: `/trades/${id}`, data }),

  delete: (id: number) =>
    request<TradeMessageResponse>({ method: "DELETE", url: `/trades/${id}` }),

  /**
   * 确认预览（#493）：业务输入一律走 **query 参数**（`confirm_date` / `price` /
   * `cash_confirm_date` / `cash_platform_code`），与后端 preview 端点协议一致。
   * 零写入：只查询与计算，不落库。
   */
  preview: (id: number, params?: TradeConfirmParams) =>
    request<TradePreviewResponse>({ method: "GET", url: `/trades/${id}/preview`, params }),

  /**
   * 确认（#493）：同样是 **query 参数**协议——历史上这里发的是 JSON body，
   * 后端只读 query，故 `confirm_date`/`price` 一直被静默忽略，新增的
   * `cash_confirm_date`/`cash_platform_code`（卖出到账信息）更会整体丢失。
   * 外层响应结构保持现状，交易本体在 `trade` 字段（见 TradeConfirmResponse）。
   */
  confirm: (id: number, params?: TradeConfirmParams) =>
    request<TradeConfirmResponse>({ method: "POST", url: `/trades/${id}/confirm`, params }),

  cancel: (id: number) =>
    request<TradeMessageResponse>({ method: "POST", url: `/trades/${id}/cancel` }),

  unconfirm: (id: number) =>
    request<TradeMessageResponse>({ method: "POST", url: `/trades/${id}/unconfirm` }),

  batchRebalance: (portfolioCode: string, trades: TradeCreate[], idempotencyKey?: string) =>
    request<{ created_trades: Trade[] }>({
      method: "POST",
      url: `/portfolios/${portfolioCode}/batch-rebalance`,
      data: { trades },
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    }),
};
