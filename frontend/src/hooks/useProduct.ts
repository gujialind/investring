"use client";

import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { productApi, getErrorMessage } from "@/lib/api";
import type { NavAnalysisRange, ProductListParams } from "@/lib/api";
import { ProductCreate, ProductUpdate } from "@/types/product";
import { useUIStore } from "@/stores/uiStore";

const PRODUCT_QUERY_KEY = "products";

// 产品列表 Hook（参数全集见 ProductListParams，queryKey 带 params 自动按条件刷新）
// placeholderData 保留旧数据：筛选/翻页局部刷新不闪烁（规范 §14，同 useTradeList）
// options.enabled 供下拉组件懒加载（#165）：默认 true，不影响页面级调用方
// #214：加载失败经 useEffect 监听 isError 弹全局 toast（v5 移除 useQuery onError 的官方等价写法），
// 调用方须同时读 isError 区分「请求失败」与「真的为空」，不得把失败渲染成空态
export function useProductList(params?: ProductListParams, options?: { enabled?: boolean }) {
  const addToast = useUIStore((state) => state.addToast);
  const query = useQuery({
    queryKey: [PRODUCT_QUERY_KEY, "list", params],
    queryFn: () => productApi.list(params),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
    enabled: options?.enabled ?? true,
  });

  const { isError, error } = query;
  useEffect(() => {
    if (isError) {
      addToast({
        type: "error",
        title: "产品列表加载失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    }
  }, [isError, error, addToast]);

  return query;
}

// 单个产品详情 Hook
export function useProduct(code: string, market?: string) {
  return useQuery({
    queryKey: [PRODUCT_QUERY_KEY, code, market],
    queryFn: () => productApi.get(code, market),
    enabled: !!code,
    staleTime: 30 * 1000,
  });
}

// 创建产品 Hook
export function useCreateProduct() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: ProductCreate) => productApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY, "list"] });
      addToast({
        type: "success",
        title: "创建成功",
        message: "产品已创建",
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

// 更新产品 Hook（code/market 随 mutate 传入，避免闭包捕获空值）
export function useUpdateProduct() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ code, data, market }: { code: string; data: ProductUpdate; market?: string }) =>
      productApi.update(code, data, market),
    onSuccess: (_, { code }) => {
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY, code] });
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY, "list"] });
      addToast({
        type: "success",
        title: "更新成功",
        message: "产品信息已更新",
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

// 删除产品 Hook
export function useDeleteProduct() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ code, market }: { code: string; market?: string }) => productApi.delete(code, market),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY, "list"] });
      addToast({
        type: "success",
        title: "删除成功",
        message: "产品已删除",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "删除失败",
        message: getErrorMessage(error, "该产品已被使用，无法删除"),
      });
    },
  });
}

// 产品价格/净值历史 Hook（后端路径要求 market 必填）
export function useProductPrices(code?: string, market?: string, limit = 30) {
  return useQuery({
    queryKey: [PRODUCT_QUERY_KEY, "prices", code, market],
    queryFn: () => productApi.getPriceData(code!, market!, { limit }),
    enabled: !!code && !!market,
    staleTime: 5 * 60 * 1000,
  });
}

// 同步最新价格 Hook
export function useSyncProductPrice() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ code, market }: { code: string; market?: string }) =>
      productApi.syncPrice(code, market),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY] });
      addToast({
        type: "success",
        title: "同步成功",
        message: `已同步 ${data.synced_count || 0} 条价格数据`,
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "同步失败",
        message: getErrorMessage(error, "请检查数据源配置"),
      });
    },
  });
}

// 同步历史价格 Hook
export function useSyncProductHistory() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ code, market }: { code: string; market?: string }) =>
      productApi.syncHistory(code, market),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY] });
      addToast({
        type: "success",
        title: "同步成功",
        message: `已同步 ${data.synced_count || 0} 条历史价格数据`,
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "同步失败",
        message: getErrorMessage(error, "请检查数据源配置"),
      });
    },
  });
}

// --- #595 §5.3 产品详情页净值数据 hooks ---

/**
 * 区间累计净值曲线 + 六窗区间收益率；range 变化即换 key 重查。
 * keepPreviousData：切 Tab 只换曲线，六窗数值取自全量序列与 range 无关，
 * 重查期间不闪占位（#638 L2 S5）。
 */
export function useNavAnalysis(
  code: string | undefined,
  market: string | undefined,
  range: NavAnalysisRange
) {
  return useQuery({
    queryKey: [PRODUCT_QUERY_KEY, "nav-analysis", code, market, range],
    queryFn: () => productApi.getNavAnalysis(code!, market!, range),
    enabled: !!code && !!market,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60 * 1000,
  });
}
