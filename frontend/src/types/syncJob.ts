// 后台同步任务（对齐后端 SyncJobResponse，GET /api/sync-jobs/{id}）
export interface SyncJob {
  id: number;
  job_type: string;
  /** pending | running | success | partial | failed | interrupted */
  status: string;
  params?: Record<string, unknown> | null;
  total: number;
  done: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  error_message?: string | null;
  triggered_by: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

// 批量切换逐产品台账条目（#882，data_source_switch  job 的 params["results"]，
// 经 GET /api/sync-jobs/{id}/details 返回；old_source 供反向批量切回恢复）
export interface DataSourceSwitchResult {
  code: string;
  market: string;
  old_source: string | null;
  new_source: string | null;
  /** success | skipped | failed */
  status: string;
  reason?: string | null;
}
