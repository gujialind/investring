"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { dividendConfigApi, getErrorMessage } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { useUIStore } from "@/stores/uiStore";
import type { DividendConfigCreate, DividendConfigKey } from "@/types/dividend-config";

/** 确认弹窗预选解析（A12）：弹窗打开才发请求，retry:false / staleTime:0，
 *  query key 含事件键 + 权益登记日（与确认弹窗缓存约定一致，frontend/AGENTS §1.2）。 */
export function useDividendConfigResolve(
  key: DividendConfigKey,
  entitlementDate: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.dividendConfigs.resolve(key, entitlementDate),
    queryFn: () => dividendConfigApi.resolve({ ...key, entitlement_date: entitlementDate }),
    enabled: enabled && !!key.portfolio_code && !!key.product_code && !!key.platform_code,
    retry: false,
    staleTime: 0,
  });
}

/** 配置历史列表（管理弹窗用）。 */
export function useDividendConfigList(key: DividendConfigKey, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.dividendConfigs.list(key),
    queryFn: () => dividendConfigApi.list({ ...key, page: 1, page_size: 50 }),
    enabled: enabled && !!key.portfolio_code && !!key.product_code && !!key.platform_code,
    staleTime: 30 * 1000,
  });
}

/** 写入配置（配置页 / 确认沉淀共用同一 POST，A13）。失败独立 toast，不回滚已成功的确认。 */
export function useCreateDividendConfig() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: (data: DividendConfigCreate) => dividendConfigApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.dividendConfigs.root });
      addToast({ type: "success", title: "配置已保存", message: "分红方式配置已写入" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "配置保存失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}

export function useDeleteDividendConfig() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);
  return useMutation({
    mutationFn: (id: number) => dividendConfigApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.dividendConfigs.root });
      addToast({ type: "success", title: "删除成功", message: "分红方式配置已删除" });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "删除失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}