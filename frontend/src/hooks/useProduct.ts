"use client";

import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient, useQueries, keepPreviousData } from "@tanstack/react-query";
import { productApi, getErrorMessage } from "@/lib/api";
import type { NavAnalysisRange, NavHistoryItem, ProductListParams } from "@/lib/api";
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

// 单个产品详情 Hook。
// #825 L2 Nit：market 缺失时不发 GET /products/{code}（单段自动解析端点）——详情页路由契约
// 恒带 market，缺参时调用方直接渲染「缺少 market 参数」空态，请求发了也没有消费方；
// 一码多市场产品还会先撞一次 422 重试。未来若真需要无 market 的自动解析形态，再单独放开。
export function useProduct(code: string, market?: string) {
  return useQuery({
    queryKey: [PRODUCT_QUERY_KEY, code, market],
    queryFn: () => productApi.get(code, market),
    enabled: !!code && !!market,
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

// 同步历史价格 Hook（#856：可选周期；无日期=成立以来全量、不发请求体）
export function useSyncProductHistory() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ code, market, start_date, end_date }: { code: string; market?: string; start_date?: string; end_date?: string }) =>
      productApi.syncHistory(
        code,
        market,
        start_date === undefined && end_date === undefined ? undefined : { start_date, end_date }
      ),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: [PRODUCT_QUERY_KEY] });
      addToast({
        type: "success",
        title: "同步成功",
        message: `同步 ${data.synced_count || 0} 条（新增 ${data.new_count || 0} 条）`,
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
 * 区间净值曲线 + 六窗区间收益率（口径随 nav_basis，仅 accumulated 下是累计净值，
 * 见 lib/navBasis.ts）；range 变化即换 key 重查。
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

/** 历史净值分页大小（首屏 5 行 + 查看更多）；单点在此，ProductNavCards 与消费方共用同一 query key */
export const NAV_HISTORY_PAGE_SIZE = 5;

/**
 * 历史净值「首屏 N 页 + 查看更多」：按页并行查询后按页序拼接（keepPreviousData 的单页 hook
 * 不适合追加式加载；页间数据由后端分页契约保证不重叠）。#647：失败与真空态必须可区分，故同时
 * 返回 isError/error/refetch，调用方的失败臂须排在「长度为 0」之前。
 * enabled 缺省 true；现金产品（market=""）无净值端点会 404，调用方传 enabled=false 规避。
 * 页 1 的 query key 在 pages≥1 时恒定，故 ProductDetailContent 概览卡取 latestPrice（pages=1）
 * 与 ProductNavCards 历史卡的首屏查询命中同一缓存条目，react-query 去重为单次请求。
 */
export function useNavHistory(code: string, market: string, pages: number, enabled = true) {
  const queries = useQueries({
    queries: Array.from({ length: pages }, (_, i) => ({
      queryKey: [PRODUCT_QUERY_KEY, "nav-history", code, market, i + 1, NAV_HISTORY_PAGE_SIZE],
      queryFn: () =>
        productApi.getNavHistory(code, market, { page: i + 1, page_size: NAV_HISTORY_PAGE_SIZE }),
      staleTime: 5 * 60 * 1000,
      enabled,
    })),
  });
  const items: NavHistoryItem[] = queries.flatMap((q) => q.data?.items ?? []);
  const total = queries[0]?.data?.total ?? 0;
  const isError = queries.some((q) => q.isError);
  const error = queries.find((q) => q.isError)?.error ?? null;
  const refetch = () => queries.forEach((q) => void q.refetch());
  return { items, total, isError, error, refetch };
}
