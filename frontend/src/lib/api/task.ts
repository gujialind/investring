import { request } from "./client";
import { PaginatedResponse } from "@/types/common";
import { TaskExecution, TaskRunAccepted } from "@/types/log";

// 任务页已下线（#715）：list / enable / disable 三个方法此前仅 TasksContent 消费，
// 随页面删除。任务启停退到 ir-cli（`ir task enable/disable`）。保留的三个方法服务于
// 产品页「批量净值同步」与设置页「日志管理 → 执行历史 / 立即清理」。
export const taskApi = {
  // #714：手动触发改为「提交后台执行 + 轮询终态」，长任务不再被网关 60s 切断成假失败
  run: (code: string) =>
    request<TaskRunAccepted>({ method: "POST", url: `/system/tasks/${code}/run` }),

  // 单条执行记录（轮询入口：pending/running → success / failed / partial_success / interrupted）
  getExecution: (executionId: number) =>
    request<TaskExecution>({ method: "GET", url: `/system/tasks/executions/${executionId}` }),

  // 全局执行历史（跨任务，可选按 task_code 过滤）
  executionHistory: (params?: { task_code?: string; page?: number; page_size?: number }) =>
    request<PaginatedResponse<TaskExecution>>({ method: "GET", url: "/system/tasks/executions", params }),
};
