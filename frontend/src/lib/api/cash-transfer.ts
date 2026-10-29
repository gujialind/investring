import { request } from "./client";
import { CashTransferCreate, CashTransferResponse, CashTransferListItem, CashTransferCancelResponse } from "@/types/cash-transfer";
import { PaginatedResponse } from "@/types/common";

export const cashTransferApi = {
  create: (portfolioCode: string, data: CashTransferCreate) =>
    request<CashTransferResponse>({
      method: "POST",
      url: `/portfolios/${portfolioCode}/cash-transfer`,
      data,
    }),

  confirm: (portfolioCode: string, transferGroup: string) =>
    request<{ message: string; transfer_group: string; buy_trade_id: number; status: string; confirm_date: string | null }>({
      method: "POST",
      url: `/portfolios/${portfolioCode}/cash-transfer/${transferGroup}/confirm`,
    }),

  cancel: (portfolioCode: string, transferGroup: string) =>
    request<CashTransferCancelResponse>({
      method: "POST",
      url: `/portfolios/${portfolioCode}/cash-transfer/${transferGroup}/cancel`,
    }),

  list: (portfolioCode: string, params?: { page?: number; page_size?: number; transfer_group?: string }) =>
    request<PaginatedResponse<CashTransferListItem>>({
      method: "GET",
      url: `/portfolios/${portfolioCode}/cash-transfers`,
      params,
    }),
};
