"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ledgerApi, getErrorMessage } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import {
  LedgerEntryCreate,
  LedgerEntryUpdate,
  LedgerFlowCreate,
  LedgerFlowUpdate,
} from "@/types/ledger";
import { useUIStore } from "@/stores/uiStore";

// 平台市值台账型组合（issue #905 PR3）数据 hooks。
// 写侧 admin、读侧登录用户（S8）；后端 403/422 兜底，前端按角色不渲染写入口。
// 台账写后失效：台账整段（entries/flows/totals）+ 组合详情/列表（台账收益喂详情
// total_value/total_profit 与仪表盘 R8 合并），与净值型写后失效组合同口径。

function useLedgerInvalidation(code: string) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.ledger.byPortfolio(code) });
    queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.detail(code) });
    queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.list() });
  };
}

// ==================== 读侧 ====================

export function useLedgerEntries(
  code: string,
  params?: { platform_code?: string },
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.ledger.entries(code, params),
    queryFn: () => ledgerApi.listEntries(code, params),
    enabled: !!code && (options?.enabled ?? true),
    staleTime: 30 * 1000,
  });
}

export function useLedgerFlows(
  code: string,
  params?: { platform_code?: string },
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: queryKeys.ledger.flows(code, params),
    queryFn: () => ledgerApi.listFlows(code, params),
    enabled: !!code && (options?.enabled ?? true),
    staleTime: 30 * 1000,
  });
}

export function useLedgerTotals(code: string, asOfDate?: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.ledger.totals(code, asOfDate),
    queryFn: () => ledgerApi.getTotals(code, asOfDate ? { as_of_date: asOfDate } : undefined),
    enabled: enabled && !!code,
    staleTime: 30 * 1000,
  });
}

// ==================== 写侧：市值台账 ====================

export function useCreateLedgerEntry(code: string) {
  const invalidate = useLedgerInvalidation(code);
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: (data: LedgerEntryCreate) => ledgerApi.createEntry(code, data),
    onSuccess: () => {
      invalidate();
      addToast({ type: "success", title: "登记成功", message: "平台市值已登记" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "登记失败", message: getErrorMessage(error, "请检查输入信息") });
    },
  });
}

export function useUpdateLedgerEntry(code: string) {
  const invalidate = useLedgerInvalidation(code);
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: LedgerEntryUpdate }) =>
      ledgerApi.updateEntry(code, id, data),
    onSuccess: () => {
      invalidate();
      addToast({ type: "success", title: "更新成功", message: "市值记录已更新" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "更新失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}

export function useDeleteLedgerEntry(code: string) {
  const invalidate = useLedgerInvalidation(code);
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: (id: number) => ledgerApi.deleteEntry(code, id),
    onSuccess: () => {
      invalidate();
      addToast({ type: "success", title: "删除成功", message: "市值记录已删除" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "删除失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}

// ==================== 写侧：本金进出 ====================

// S4：净入金为负时后端在写入响应附 warnings，前端如实呈现（不静默吞掉）。
function surfaceFlowWarnings(
  warnings: string[] | undefined,
  addToast: (t: { type: "warning"; title: string; message: string }) => void
) {
  if (warnings && warnings.length > 0) {
    addToast({ type: "warning", title: "收益基数异常", message: warnings.join("；") });
  }
}

export function useCreateLedgerFlow(code: string) {
  const invalidate = useLedgerInvalidation(code);
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: (data: LedgerFlowCreate) => ledgerApi.createFlow(code, data),
    onSuccess: (data) => {
      invalidate();
      addToast({ type: "success", title: "登记成功", message: "本金进出已登记" });
      surfaceFlowWarnings(data.warnings, addToast);
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "登记失败", message: getErrorMessage(error, "请检查输入信息") });
    },
  });
}

export function useUpdateLedgerFlow(code: string) {
  const invalidate = useLedgerInvalidation(code);
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: LedgerFlowUpdate }) =>
      ledgerApi.updateFlow(code, id, data),
    onSuccess: (data) => {
      invalidate();
      addToast({ type: "success", title: "更新成功", message: "进出记录已更新" });
      surfaceFlowWarnings(data.warnings, addToast);
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "更新失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}

export function useDeleteLedgerFlow(code: string) {
  const invalidate = useLedgerInvalidation(code);
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: (id: number) => ledgerApi.deleteFlow(code, id),
    onSuccess: () => {
      invalidate();
      addToast({ type: "success", title: "删除成功", message: "进出记录已删除" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "删除失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}