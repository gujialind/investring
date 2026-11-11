import { request } from "./client";
import {
  SnapshotGenerationResult,
  RecalculationResult,
  SnapshotValidationResult,
  SnapshotStatusResponse,
  SnapshotListResponse,
  SnapshotCatchUpResult,
  SnapshotGenerateNextResult,
  RecalculateAsyncSubmitResult,
  BulkDeleteDryRunResult,
  BulkDeleteResult,
} from "@/types/snapshot";

export const snapshotApi = {
  // 单日生成
  generate: (portfolioCode: string, targetDate: string) =>
    request<SnapshotGenerationResult>({
      method: "POST",
      url: "/snapshots/generate",
      data: { portfolio_code: portfolioCode, target_date: targetDate },
    }),

  // 区间重算（同步模式，大区间易超时；前端页面已改用 recalculateAsync，此入口留给 CLI 以外的小区间调用）
  // confirm 必须由调用方显式传入：后端未收到 confirm=true 时只返回 422 CONFIRM_REQUIRED 与区间预览（#855）
  recalculate: (
    portfolioCode: string | null,
    startDate: string,
    endDate: string,
    confirm: boolean
  ) =>
    request<RecalculationResult>({
      method: "POST",
      url: "/snapshots/recalculate",
      params: { confirm },
      data: {
        portfolio_code: portfolioCode,
        start_date: startDate,
        end_date: endDate,
      },
    }),

  // 预检验证
  validate: (portfolioCode: string, targetDate: string) =>
    request<SnapshotValidationResult>({
      method: "GET",
      url: "/snapshots/validation",
      params: { portfolio_code: portfolioCode, target_date: targetDate },
    }),

  // 查询状态
  getStatus: (portfolioCode: string) =>
    request<SnapshotStatusResponse>({
      method: "GET",
      url: `/snapshots/portfolios/${portfolioCode}/status`,
    }),

  // 快照历史列表（#146）
  list: (
    portfolioCode: string,
    params?: { start_date?: string; end_date?: string; limit?: number }
  ) =>
    request<SnapshotListResponse>({
      method: "GET",
      url: `/snapshots/portfolios/${portfolioCode}/list`,
      params,
    }),

  // 生成下一交易日快照（#146）
  generateNext: (portfolioCode: string) =>
    request<SnapshotGenerateNextResult>({
      method: "POST",
      url: "/snapshots/generate-next",
      data: { portfolio_code: portfolioCode },
    }),

  // 逐交易日追平至 to_date（#146）
  catchUp: (portfolioCode: string, toDate: string) =>
    request<SnapshotCatchUpResult>({
      method: "POST",
      url: "/snapshots/catch-up",
      data: { portfolio_code: portfolioCode, to_date: toDate },
    }),

  // 异步区间重算（#146）：提交后台任务，经 syncJobApi.get 轮询终态
  recalculateAsync: (portfolioCode: string, startDate: string, endDate: string) =>
    request<RecalculateAsyncSubmitResult>({
      method: "POST",
      url: "/snapshots/recalculate-async",
      data: {
        portfolio_code: portfolioCode,
        start_date: startDate,
        end_date: endDate,
      },
    }),

  // 删除快照
  delete: (portfolioCode: string, snapshotDate: string) =>
    request<{ success: boolean; message: string }>({
      method: "DELETE",
      url: `/snapshots/${portfolioCode}/${snapshotDate}`,
    }),

  // 批量删除（#146）：dry_run 纯预览，confirm 实际执行；返回类型随 mode 字面量收窄
  deleteBulk: <M extends "dry_run" | "confirm">(
    portfolioCode: string,
    fromDate: string,
    mode: M
  ) =>
    request<M extends "dry_run" ? BulkDeleteDryRunResult : BulkDeleteResult>({
      method: "DELETE",
      url: `/snapshots/${portfolioCode}/bulk/${fromDate}`,
      params: mode === "dry_run" ? { dry_run: true } : { confirm: true },
    }),
};
