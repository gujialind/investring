"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { cashTransferApi, getErrorMessage } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { CashTransferCreate } from "@/types/cash-transfer";
import { useUIStore } from "@/stores/uiStore";

const CASH_TRANSFER_QUERY_KEY = "cash-transfers";

// 现金转移列表 Hook
export function useCashTransferList(portfolioCode: string, params?: { page?: number; page_size?: number }) {
  return useQuery({
    queryKey: [CASH_TRANSFER_QUERY_KEY, "list", portfolioCode, params],
    queryFn: () => cashTransferApi.list(portfolioCode, params),
    enabled: !!portfolioCode,
    staleTime: 30 * 1000,
  });
}

// 创建现金转移 Hook
export function useCreateCashTransfer(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: CashTransferCreate) => cashTransferApi.create(portfolioCode, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [CASH_TRANSFER_QUERY_KEY, "list", portfolioCode] });
      queryClient.invalidateQueries({ queryKey: ["positions", portfolioCode] });
      // 现金流水页读侧（列表/四卡/构成，#727 Blocker）：转移两腿都是现金流水行
      queryClient.invalidateQueries({ queryKey: queryKeys.cashFlows.root });
      addToast({
        type: "success",
        title: "转移成功",
        message: "现金转移已创建",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "转移失败",
        message: getErrorMessage(error, "请检查输入信息"),
      });
    },
  });
}

// 确认跨天转移 Hook
export function useConfirmCashTransfer(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (transferGroup: string) => cashTransferApi.confirm(portfolioCode, transferGroup),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [CASH_TRANSFER_QUERY_KEY, "list", portfolioCode] });
      queryClient.invalidateQueries({ queryKey: ["positions", portfolioCode] });
      queryClient.invalidateQueries({ queryKey: queryKeys.cashFlows.root });
      addToast({
        type: "success",
        title: "确认成功",
        message: "跨天转移已确认",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "确认失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    },
  });
}
