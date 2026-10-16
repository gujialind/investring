"use client";

import { useQuery } from "@tanstack/react-query";
import { logApi } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

// 日志分页查询参数（进 queryKey，换页/换每页条数即换键重取，口径照 useProductList）
export interface LogPageParams {
  page?: number;
  page_size?: number;
}

// 登录日志（GET /system/logs/login，admin-only，返回 {items,total,page,page_size}）
export function useLoginLogs(params?: LogPageParams) {
  return useQuery({
    queryKey: queryKeys.logs.login(params),
    queryFn: () => logApi.loginLogs(params),
    staleTime: 10 * 1000,
  });
}

// 审计日志（GET /system/logs/audit）
export function useAuditLogs(params?: LogPageParams) {
  return useQuery({
    queryKey: queryKeys.logs.audit(params),
    queryFn: () => logApi.auditLogs(params),
    staleTime: 10 * 1000,
  });
}

// 系统错误日志（GET /system/logs/error）
export function useErrorLogs(params?: LogPageParams) {
  return useQuery({
    queryKey: queryKeys.logs.error(params),
    queryFn: () => logApi.errorLogs(params),
    staleTime: 10 * 1000,
  });
}
