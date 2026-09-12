import { request } from "./client";
import {
  ShareChangeEvent,
  ShareChangeEventCreate,
  ShareChangeEventUpdate,
  ShareChangeEventPreviewResponse,
} from "@/types/share-change-event";
import { PaginatedResponse } from "@/types/common";

export interface ShareChangeEventListParams {
  page?: number;
  page_size?: number;
  portfolio_code?: string;
  status?: string;
  event_type?: string;
  product_code?: string;
  /** 逗号分隔 `code|market` 复合多选（#155 契约），与 product_code 互斥 */
  products?: string;
  platform_code?: string;
  ex_date_start?: string;
  ex_date_end?: string;
}

export const shareChangeEventApi = {
  list: (params?: ShareChangeEventListParams) =>
    request<PaginatedResponse<ShareChangeEvent>>({
      method: "GET",
      url: "/share-change-events",
      params,
    }),

  get: (id: number) =>
    request<ShareChangeEvent>({ method: "GET", url: `/share-change-events/${id}` }),

  /** 确认前预览（#424）：与真实确认共用后端计算实现，返回值即确认后将落库的值 */
  preview: (id: number) =>
    request<ShareChangeEventPreviewResponse>({
      method: "GET",
      url: `/share-change-events/${id}/preview`,
    }),

  create: (data: ShareChangeEventCreate, options?: { forceCover?: boolean }) =>
    request<ShareChangeEvent>({
      method: "POST",
      url: "/share-change-events",
      data,
      // 平台级事件未覆盖全部持仓平台时，后端默认阻断（PLATFORM_NOT_COVERED）；
      // force_cover=true 降为 warning 强制提交
      params: options?.forceCover ? { force_cover: true } : undefined,
    }),

  update: (id: number, data: ShareChangeEventUpdate) =>
    request<ShareChangeEvent>({ method: "PUT", url: `/share-change-events/${id}`, data }),

  confirm: (id: number) =>
    request<{ message: string; event: ShareChangeEvent }>({
      method: "POST",
      url: `/share-change-events/${id}/confirm`,
    }),

  unconfirm: (id: number) =>
    request<{ message: string }>({
      method: "POST",
      url: `/share-change-events/${id}/unconfirm`,
    }),

  cancel: (id: number) =>
    request<{ message: string }>({
      method: "POST",
      url: `/share-change-events/${id}/cancel`,
    }),

  delete: (id: number) =>
    request<{ message: string }>({
      method: "DELETE",
      url: `/share-change-events/${id}`,
    }),
};
