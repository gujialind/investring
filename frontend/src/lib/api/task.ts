import { request } from "./client";
import { PaginatedResponse } from "@/types/common";
import { ScheduledTask, TaskExecution, TaskRunAccepted } from "@/types/log";

export const taskApi = {
  // 后端返回分页对象 {items, total, page, page_size}
  list: (params?: { page?: number; page_size?: number }) =>
    request<PaginatedResponse<ScheduledTask>>({ method: "GET", url: "/system/tasks", params }),

  // #714：手动触发改为「提交后台执行 + 轮询终态」，长任务不再被网关 60s 切断成假失败
  run: (code: string) =>
    request<TaskRunAccepted>({ method: "POST", url: `/system/tasks/${code}/run` }),

  // 单条执行记录（轮询入口：pending/running → success / failed / partial_success / interrupted）
  getExecution: (executionId: number) =>
    request<TaskExecution>({ method: "GET", url: `/system/tasks/executions/${executionId}` }),

  enable: (code: string) =>
    request<{ message: string }>({ method: "POST", url: `/system/tasks/${code}/enable` }),

  disable: (code: string) =>
    request<{ message: string }>({ method: "POST", url: `/system/tasks/${code}/disable` }),

  // 全局执行历史（跨任务，可选按 task_code 过滤）
  executionHistory: (params?: { task_code?: string; page?: number; page_size?: number }) =>
    request<PaginatedResponse<TaskExecution>>({ method: "GET", url: "/system/tasks/executions", params }),
};
