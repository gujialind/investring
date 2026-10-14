// 字段与后端 schemas/log.py、schemas/task.py 严格对齐（issue：前端曾把真实字段当"历史兼容"别名，方向颠倒）

export interface LoginLog {
  id: number;
  investor_code: string;
  action?: string;
  status?: string; // "success" | "failed"
  ip_address?: string;
  user_agent?: string;
  failure_reason?: string;
  created_at?: string;
}

export interface AuditLog {
  id: number;
  investor_code: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  resource_name?: string;
  old_value?: string;
  new_value?: string;
  ip_address?: string;
  created_at?: string;
}

export interface ErrorLog {
  id: number;
  error_type: string;
  error_code?: string;
  error_message: string;
  error_stack?: string;
  request_path?: string;
  request_method?: string;
  request_params?: string;
  investor_code?: string;
  ip_address?: string;
  created_at?: string;
}

// 对齐后端 TaskResponse（主键是 code，无 id 列；cron 字段名为 cron_expr）
export interface ScheduledTask {
  code: string;
  name: string;
  description?: string;
  cron_expr?: string;
  is_enabled: boolean;
  last_run_at?: string;
  last_run_status?: string;
  next_run_at?: string;
  timeout_seconds?: number;
  created_at?: string;
  updated_at?: string;
}

// 对齐后端 TaskExecutionLogResponse
export interface TaskExecution {
  id: number;
  task_code: string;
  trigger_type?: string;
  status: string; // "pending" | "running" | "success" | "failed" | "partial_success" | "interrupted"
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  records_total?: number;
  records_success?: number;
  records_failed?: number;
  error_message?: string;
  error_stack?: string;
  created_at?: string;
}

// 手动触发的异步受理回执（#714）：请求返回时任务还没跑完，
// 终态需经 GET /api/system/tasks/executions/{execution_id} 轮询
export interface TaskRunAccepted {
  execution_id: number;
  task_code: string;
  status: string;
  message: string;
}
