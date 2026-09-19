import { request } from "./client";
import {
  Subscription,
  SubscriptionCreate,
  SubscriptionUpdate,
  SubscriptionPreviewResponse,
} from "@/types/subscription";
import { PaginatedResponse } from "@/types/common";

/**
 * 申赎列表查询参数（#125 服务端筛选）。
 * axios 会丢弃 undefined 值，空筛选自然不传参。
 */
export interface SubscriptionListParams {
  page?: number;
  page_size?: number;
  portfolio_code?: string;
  status?: string;
  sub_type?: string;
  investor_code?: string;
  platform_code?: string;
  apply_date_start?: string;
  apply_date_end?: string;
  confirm_date_start?: string;
  confirm_date_end?: string;
}

export const subscriptionApi = {
  list: (params?: SubscriptionListParams) =>
    request<PaginatedResponse<Subscription>>({ method: "GET", url: "/subscriptions", params }),

  get: (id: number) =>
    request<Subscription>({ method: "GET", url: `/subscriptions/${id}` }),

  create: (data: SubscriptionCreate) =>
    request<Subscription>({ method: "POST", url: "/subscriptions", data }),

  update: (id: number, data: SubscriptionUpdate) =>
    request<Subscription>({ method: "PUT", url: `/subscriptions/${id}`, data }),

  delete: (id: number) =>
    request<void>({ method: "DELETE", url: `/subscriptions/${id}` }),

  preview: (id: number) =>
    request<SubscriptionPreviewResponse>({ method: "GET", url: `/subscriptions/${id}/preview` }),

  confirm: (id: number) =>
    request<Subscription>({ method: "POST", url: `/subscriptions/${id}/confirm` }),

  /** 后端只回 `{message}`（无 portfolio_code/status）——失效面须从调用方入参取 code（#519） */
  cancel: (id: number) =>
    request<{ message: string }>({ method: "POST", url: `/subscriptions/${id}/cancel` }),

  unconfirm: (id: number) =>
    request<{ message: string }>({ method: "POST", url: `/subscriptions/${id}/unconfirm` }),
};
