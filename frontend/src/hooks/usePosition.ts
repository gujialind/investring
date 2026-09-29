"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { positionApi, getErrorMessage } from "@/lib/api";
import { PositionCreate, PositionUpdate } from "@/types/position";
import { useUIStore } from "@/stores/uiStore";

const POSITION_QUERY_KEY = "positions";

export function usePositionList(
  portfolioCode: string,
  params?: { page?: number; page_size?: number; snapshot_date?: string }
) {
  return useQuery({
    queryKey: [POSITION_QUERY_KEY, portfolioCode, "list", params],
    queryFn: () => positionApi.list(portfolioCode, params),
    enabled: !!portfolioCode,
    staleTime: 30 * 1000,
  });
}

// 产品可用份额（卖出口径，issue #67）——后端实时计算，短缓存
export function useAvailableShares(portfolioCode: string, productCode: string, enabled = true) {
  return useQuery({
    queryKey: [POSITION_QUERY_KEY, portfolioCode, "available-shares", productCode],
    queryFn: () => positionApi.getAvailableShares(portfolioCode, productCode),
    enabled: enabled && !!portfolioCode && !!productCode,
    staleTime: 10 * 1000,
  });
}

// 投资人可用份额（赎回口径，issue #67）
export function useInvestorAvailableShares(portfolioCode: string, investorCode: string, enabled = true) {
  return useQuery({
    queryKey: [POSITION_QUERY_KEY, portfolioCode, "investor-available-shares", investorCode],
    queryFn: () => positionApi.getInvestorAvailableShares(portfolioCode, investorCode),
    enabled: enabled && !!portfolioCode && !!investorCode,
    staleTime: 10 * 1000,
  });
}

export function useCreatePosition() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: PositionCreate) => positionApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [POSITION_QUERY_KEY] });
      addToast({
        type: "success",
        title: "创建成功",
        message: "持仓已创建",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "创建失败",
        message: getErrorMessage(error, "请检查输入信息"),
      });
    },
  });
}

export function useUpdatePosition() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: PositionUpdate }) =>
      positionApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [POSITION_QUERY_KEY] });
      addToast({
        type: "success",
        title: "更新成功",
        message: "持仓信息已更新",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "更新失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    },
  });
}

// #595 §4.5：更新现金市值（现金重估，写 manual_market_value 绝对替换）
// 返回完整响应供调用方处理 warnings / requires_snapshot_regen。
// 注意：本 hook 只在 onSuccess 做缓存失效，不内置错误 toast——调用方必须自带
// onError 反馈（当前唯一调用方 CashMarketValueUpdateDialog 已自带）；新增第二个
// 调用点时切勿遗漏，否则失败会静默。
export function useUpdateCashPosition(portfolioCode: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ amount, platformCode, updateDate }: {
      amount: number;
      platformCode: string;
      updateDate?: string;
    }) => positionApi.updateCashPosition(portfolioCode, amount, platformCode, updateDate),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [POSITION_QUERY_KEY, portfolioCode] });
    },
  });
}

// #595 §4.5：查询现金手动覆盖记录
export function useListCashOverrides(
  portfolioCode: string,
  params?: { platform_code?: string; start_date?: string; end_date?: string },
  enabled = true,
) {
  return useQuery({
    queryKey: [POSITION_QUERY_KEY, portfolioCode, "cash-overrides", params],
    queryFn: () => positionApi.listCashOverrides(portfolioCode, params),
    enabled: enabled && !!portfolioCode,
    staleTime: 10 * 1000,
  });
}

// #595 §4.5：撤销现金手动覆盖
export function useDeleteCashOverride(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ platformCode, updateDate }: { platformCode: string; updateDate: string }) =>
      positionApi.deleteCashOverride(portfolioCode, platformCode, updateDate),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [POSITION_QUERY_KEY, portfolioCode] });
      addToast({ type: "success", title: "已撤销", message: "覆盖记录已删除，回退到自然计算值" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "撤销失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}

// #595 组合详情页双视图：按产品 / 按平台聚合持仓（当前视图惰性查询）
export function useHoldingsByProduct(portfolioCode: string, enabled = true) {
  return useQuery({
    queryKey: [POSITION_QUERY_KEY, portfolioCode, "holdings", "by-product"],
    queryFn: () => positionApi.getHoldingsByProduct(portfolioCode),
    enabled: enabled && !!portfolioCode,
    staleTime: 30 * 1000,
  });
}

export function useHoldingsByPlatform(portfolioCode: string, enabled = true) {
  return useQuery({
    queryKey: [POSITION_QUERY_KEY, portfolioCode, "holdings", "by-platform"],
    queryFn: () => positionApi.getHoldingsByPlatform(portfolioCode),
    enabled: enabled && !!portfolioCode,
    staleTime: 30 * 1000,
  });
}
