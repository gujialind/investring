import { request } from "./client";
import { PaginatedResponse } from "@/types/common";
import { LoginLog, AuditLog, ErrorLog } from "@/types/log";

// 日志查询通用分页参数
interface LogQueryParams {
  page?: number;
  page_size?: number;
}

// 三个日志端点后端统一返回 Paginated*Response（{items,total,page,page_size}，#512）。
// 此前封装用 unwrap 丢掉分页元数据只回 items[]，logApi 零消费方，故直接返回完整分页对象，
// 让「日志管理」Tab 能拿到 total 驱动 PaginationBar（#715）。
export const logApi = {
  loginLogs: (params?: LogQueryParams) =>
    request<PaginatedResponse<LoginLog>>({ method: "GET", url: "/system/logs/login", params }),

  auditLogs: (params?: LogQueryParams) =>
    request<PaginatedResponse<AuditLog>>({ method: "GET", url: "/system/logs/audit", params }),

  errorLogs: (params?: LogQueryParams) =>
    request<PaginatedResponse<ErrorLog>>({ method: "GET", url: "/system/logs/error", params }),

  // 任务执行历史请用 taskApi.executionHistory（GET /system/tasks/executions），
  // 后端不存在 /system/logs/task 端点
};
