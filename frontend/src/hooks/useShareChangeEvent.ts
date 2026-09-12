"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { shareChangeEventApi, getErrorMessage, ApiException, ShareChangeEventListParams } from "@/lib/api";
import { ShareChangeEventCreate, ShareChangeEventUpdate } from "@/types/share-change-event";
import { queryKeys } from "@/lib/queryKeys";
import { useUIStore } from "@/stores/uiStore";

// 份额变动事件列表 Hook（服务端筛选 + 分页，#274）
export function useShareChangeEventList(portfolioCode: string, params?: ShareChangeEventListParams) {
  const fullParams: ShareChangeEventListParams = { portfolio_code: portfolioCode, ...params };
  return useQuery({
    queryKey: queryKeys.shareChangeEvents.list(portfolioCode, fullParams),
    queryFn: () => shareChangeEventApi.list(fullParams),
    enabled: !!portfolioCode,
    staleTime: 30 * 1000,
  });
}

// 确认前预览 Hook（#424）：确认弹窗打开时才发请求；禁用重试，错误即时展示在弹窗内。
// staleTime=0 保证重开弹窗必 refetch——预览值即确认值，不得基于过期值确认（同 useTradePreview）
export function useShareChangeEventPreview(id: number | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.shareChangeEvents.preview(id ?? 0),
    queryFn: () => shareChangeEventApi.preview(id!),
    enabled: enabled && !!id,
    retry: false,
    staleTime: 0,
  });
}

// 创建份额变动事件 Hook（支持 force_cover 强制提交）
export function useCreateShareChangeEvent(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ data, forceCover }: { data: ShareChangeEventCreate; forceCover?: boolean }) =>
      shareChangeEventApi.create(data, { forceCover }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.shareChangeEvents.byPortfolio(portfolioCode) });
      addToast({
        type: "success",
        title: "创建成功",
        message: "份额变动事件已创建",
      });
    },
    onError: (error: unknown) => {
      // PLATFORM_NOT_COVERED：调用方会弹确认框引导 force_cover 重试，此处不叠加 toast
      if (error instanceof ApiException && error.code === "PLATFORM_NOT_COVERED") {
        return;
      }
      addToast({
        type: "error",
        title: "创建失败",
        message: getErrorMessage(error, "请检查输入数据后重试"),
      });
    },
  });
}

// 更新份额变动事件 Hook（#342：pending 事件 PUT 直改，同调仓 #182 / 申赎 #202 口径）
// 注：pending 事件不影响持仓/可用现金，无需失效 positions（区别于 useUpdateTrade）
export function useUpdateShareChangeEvent(portfolioCode: string, id: number) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: ShareChangeEventUpdate) => shareChangeEventApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.shareChangeEvents.byPortfolio(portfolioCode) });
      addToast({
        type: "success",
        title: "更新成功",
        message: "份额变动事件已更新",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "更新失败",
        message: getErrorMessage(error, "请检查输入数据后重试"),
      });
    },
  });
}

// 确认份额变动事件 Hook
export function useConfirmShareChangeEvent(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (id: number) => shareChangeEventApi.confirm(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.shareChangeEvents.byPortfolio(portfolioCode) });
      addToast({
        type: "success",
        title: "确认成功",
        message: "份额变动事件已确认",
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

// 取消确认份额变动事件 Hook（#274：后端已实现，前端补链）
export function useUnconfirmShareChangeEvent(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (id: number) => shareChangeEventApi.unconfirm(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.shareChangeEvents.byPortfolio(portfolioCode) });
      addToast({
        type: "success",
        title: "取消确认成功",
        message: "份额变动事件已回退为待确认",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "取消确认失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    },
  });
}

// 取消份额变动事件 Hook
export function useCancelShareChangeEvent(portfolioCode: string) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (id: number) => shareChangeEventApi.cancel(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.shareChangeEvents.byPortfolio(portfolioCode) });
      addToast({
        type: "success",
        title: "取消成功",
        message: "份额变动事件已取消",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "取消失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    },
  });
}
