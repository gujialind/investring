export interface SnapshotValidationCheck {
  check_type: string;
  status: "passed" | "failed" | "warning";
  message: string;
}

export interface SnapshotValidationResult {
  portfolio_code: string;
  target_date: string;
  is_valid: boolean;
  checks: SnapshotValidationCheck[];
}

// 后端非阻断告警（issue #71：如负现金）
export type SnapshotWarning = Record<string, unknown>;

// auto_confirm_after_snapshot 逐条结果（#305：成功/失败均透传，失败携 code）
export type AutoConfirmEntry = Record<string, unknown>;

export interface SnapshotGenerationResult {
  success: boolean;
  message: string;
  portfolio_code: string;
  snapshot_date: string;
  total_value?: number;
  total_shares?: number;
  unit_price?: number;
  warnings?: SnapshotWarning[] | null;
}

export interface RecalculationPortfolioResult {
  portfolio_code: string;
  processed_dates: string[];
  total_processed: number;
  errors: Array<{
    date: string;
    error: string;
    code?: string;
    details?: Record<string, unknown>;
  }>;
  warnings?: SnapshotWarning[] | null;
  /** #305：逐日 auto_confirm 结果（含 auto_confirm_failed 条目与 code） */
  auto_confirmed?: AutoConfirmEntry[] | null;
  /** #305：删旧快照级联回退的申赎明细 */
  cascaded_unconfirmed?: AutoConfirmEntry[] | null;
  /** #305：end_date 后存在快照时自动扩展到的实际重算终点 */
  end_date_extended_to?: string | null;
}

export interface RecalculationResult {
  success: boolean;
  message: string;
  results: RecalculationPortfolioResult[];
}

export interface SnapshotStatusResponse {
  portfolio_code: string;
  latest_snapshot_date?: string;
  total_snapshots: number;
  first_snapshot_date?: string;
  missing_dates: string[];
  /** 最新快照日之后至业务昨日应生成而未生成的 SSE 交易日（#906 trailing 缺口；台账型/无基线恒空） */
  catchup_pending_dates?: string[];
  /** 最新快照日 CASH 持仓 cash_amount < 0 的平台清单（正常为空） */
  negative_cash_platforms?: string[];
  /** 组合级自动快照开关（issue #156），随组合配置返回 */
  auto_snapshot_enabled: boolean;
}

// ---- #146 快照管理页重构 ----

export interface SnapshotListItem {
  snapshot_date: string;
  unit_price: number;
  total_shares: number;
  total_value: number;
  in_transit_total: number;
}

export interface SnapshotListResponse {
  portfolio_code: string;
  items: SnapshotListItem[];
  /** 过滤后全量计数（limit 截断前），total > items.length 即被截断 */
  total: number;
  limit: number;
}

export interface SnapshotCatchUpResult {
  portfolio_code: string;
  to_date: string;
  generated_count: number;
  generated_dates: string[];
  latest_snapshot_date?: string | null;
  message?: string | null;
  failed_date?: string | null;
  error?: string | null;
  /** #305：中断错误的结构化 code/details（error 仍为消息文本） */
  error_code?: string | null;
  error_details?: Record<string, unknown> | null;
  /** #305：逐日生成累积的非阻断告警（每条带 date） */
  warnings?: SnapshotWarning[] | null;
  auto_confirmed?: AutoConfirmEntry[] | null;
}

export interface SnapshotGenerateNextResult {
  success: boolean;
  message: string;
  portfolio_code: string;
  generated_date: string;
  total_value?: number;
  total_shares?: number;
  unit_price?: number;
  warnings?: SnapshotWarning[] | null;
  auto_confirmed?: AutoConfirmEntry[] | null;
}

export interface RecalculateAsyncSubmitResult {
  job_id: number;
  status: string;
  message: string;
}

export interface BulkDeleteDryRunResult {
  dry_run: true;
  portfolio_code: string;
  from_date: string;
  count: number;
  /** 将删除的快照日期（倒序） */
  snapshot_dates: string[];
}

export interface BulkDeleteResult {
  success: boolean;
  message: string;
  deleted_count: number;
  details?: Array<{
    snapshot_date: string;
    deleted: number;
    cascaded_subs: number;
    cascaded_events: number;
  }>;
}
