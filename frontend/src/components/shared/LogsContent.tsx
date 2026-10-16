"use client";

import { useState, type ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Loader2, Trash2 } from "lucide-react";
import { cn, formatDate, getStatusBadgeVariant } from "@/lib/utils";
import PaginationBar from "@/components/shared/PaginationBar";
import QueryErrorState from "@/components/shared/QueryErrorState";
import EmptyState from "@/components/shared/EmptyState";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import { useLoginLogs, useAuditLogs, useErrorLogs } from "@/hooks/useLog";
import { useTaskExecutions, useRunTask, useWatchedTaskExecution } from "@/hooks/useTask";

interface LogsContentProps {
  variant?: "desktop" | "mobile";
}

// 执行记录状态文案（迁移自已下线的 TasksContent，#715）：#714 起手动触发为异步，
// 故含 pending / interrupted 两态。徽章配色仍由 getStatusBadgeVariant 单点决定。
const EXECUTION_STATUS_LABELS: Record<string, string> = {
  pending: "排队中",
  running: "执行中",
  success: "成功",
  failed: "失败",
  partial_success: "部分成功",
  interrupted: "已中断",
};

// 触发方式徽章（#406 / #715）：手动/自动/重试/联动是「来源」标识，无状态语义，故按
// docs/design/visual-spec.md §1.3 末段用 neutral / outline，不占用状态色。retry/cascade
// 是 #715 后端新增的定时重试与快照联动补跑，执行历史在此可直接区分首跑/重试/联动。
const TRIGGER_LABELS: Record<string, string> = {
  scheduled: "自动",
  manual: "手动",
  retry: "重试",
  cascade: "联动",
};

function getTriggerBadgeVariant(triggerType?: string): "neutral" | "outline" {
  return triggerType === "scheduled" ? "neutral" : "outline";
}

// 执行历史 task_code 过滤选项（与后端 init_tasks 的四个定时任务码对齐）
const TASK_CODE_OPTIONS = [
  { value: "nav_sync", label: "净值同步" },
  { value: "snapshot_generate", label: "快照生成" },
  { value: "trading_calendar_sync", label: "交易日历同步" },
  { value: "log_cleanup", label: "日志清理" },
];
const ALL_TASKS = "all";

/**
 * 分页表格面板的统一外壳（#715）：加载态 / 失败态 / 空态 / 数据表 + 分页条 + 局部刷新遮罩。
 *
 * 口径（frontend/AGENTS.md §1.2）：① 失败不得伪装成空态——isError 分支排在 length===0
 * 之前；② 换页/重取在途（isFetching && !isLoading）保留旧行集但容器 opacity-50 + 右上角
 * Loader2，并显式写 aria-busy（定位器契约禁止按 Tailwind 工具类定位，故它同时是状态标记与
 * E2E 锚点）。table 由调用方给出（各日志类型列不同），本组件只管状态分支与分页 chrome。
 */
function PaginatedPanel({
  variant,
  isLoading,
  isFetching,
  isError,
  error,
  refetch,
  isEmpty,
  emptyMessage,
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  table,
}: {
  variant: "desktop" | "mobile";
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
  isEmpty: boolean;
  emptyMessage: string;
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  table: ReactNode;
}) {
  const busy = isFetching && !isLoading;
  return (
    <>
      <div className={cn("relative", variant === "mobile" && "overflow-x-auto")} aria-busy={busy ? "true" : "false"}>
        {busy && (
          <Loader2 className="absolute right-2 top-2 z-10 h-4 w-4 animate-spin text-muted-foreground" />
        )}
        <div className={cn(busy && "opacity-50")}>
          {isLoading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              加载中...
            </div>
          ) : isError ? (
            <QueryErrorState error={error} onRetry={refetch} />
          ) : isEmpty ? (
            <EmptyState message={emptyMessage} />
          ) : (
            table
          )}
        </div>
      </div>
      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        variant={variant}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </>
  );
}

function LoginLogsPanel({ variant }: { variant: "desktop" | "mobile" }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { data, isLoading, isFetching, isError, error, refetch } = useLoginLogs({
    page,
    page_size: pageSize,
  });
  const items = data?.items ?? [];
  return (
    <Card data-testid="login-logs-card">
      <CardHeader>
        <CardTitle>登录日志</CardTitle>
        <CardDescription>投资人登录成功与失败记录</CardDescription>
      </CardHeader>
      <CardContent>
        <PaginatedPanel
          variant={variant}
          isLoading={isLoading}
          isFetching={isFetching}
          isError={isError}
          error={error}
          refetch={refetch}
          isEmpty={items.length === 0}
          emptyMessage="暂无登录日志"
          total={data?.total ?? 0}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>投资人</TableHead>
                  <TableHead>动作</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>IP 地址</TableHead>
                  <TableHead>失败原因</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="font-medium">{log.investor_code}</TableCell>
                    <TableCell>{log.action || "--"}</TableCell>
                    <TableCell>
                      {log.status ? (
                        <Badge variant={getStatusBadgeVariant(log.status)}>
                          {log.status === "success" ? "成功" : log.status === "failed" ? "失败" : log.status}
                        </Badge>
                      ) : (
                        "--"
                      )}
                    </TableCell>
                    <TableCell>{log.ip_address || "--"}</TableCell>
                    <TableCell>{log.failure_reason || "--"}</TableCell>
                    <TableCell>{log.created_at ? formatDate(log.created_at) : "--"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          }
        />
      </CardContent>
    </Card>
  );
}

function AuditLogsPanel({ variant }: { variant: "desktop" | "mobile" }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { data, isLoading, isFetching, isError, error, refetch } = useAuditLogs({
    page,
    page_size: pageSize,
  });
  const items = data?.items ?? [];
  return (
    <Card data-testid="audit-logs-card">
      <CardHeader>
        <CardTitle>审计日志</CardTitle>
        <CardDescription>数据变更操作留痕</CardDescription>
      </CardHeader>
      <CardContent>
        <PaginatedPanel
          variant={variant}
          isLoading={isLoading}
          isFetching={isFetching}
          isError={isError}
          error={error}
          refetch={refetch}
          isEmpty={items.length === 0}
          emptyMessage="暂无审计日志"
          total={data?.total ?? 0}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>投资人</TableHead>
                  <TableHead>动作</TableHead>
                  <TableHead>资源类型</TableHead>
                  <TableHead>资源</TableHead>
                  <TableHead>IP 地址</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="font-medium">{log.investor_code}</TableCell>
                    <TableCell>{log.action}</TableCell>
                    <TableCell>{log.resource_type}</TableCell>
                    <TableCell>{log.resource_name || log.resource_id || "--"}</TableCell>
                    <TableCell>{log.ip_address || "--"}</TableCell>
                    <TableCell>{log.created_at ? formatDate(log.created_at) : "--"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          }
        />
      </CardContent>
    </Card>
  );
}

function ErrorLogsPanel({ variant }: { variant: "desktop" | "mobile" }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { data, isLoading, isFetching, isError, error, refetch } = useErrorLogs({
    page,
    page_size: pageSize,
  });
  const items = data?.items ?? [];
  return (
    <Card data-testid="error-logs-card">
      <CardHeader>
        <CardTitle>错误日志</CardTitle>
        <CardDescription>系统错误与异常请求留痕</CardDescription>
      </CardHeader>
      <CardContent>
        <PaginatedPanel
          variant={variant}
          isLoading={isLoading}
          isFetching={isFetching}
          isError={isError}
          error={error}
          refetch={refetch}
          isEmpty={items.length === 0}
          emptyMessage="暂无错误日志"
          total={data?.total ?? 0}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>类型</TableHead>
                  <TableHead>错误码</TableHead>
                  <TableHead>消息</TableHead>
                  <TableHead>请求</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="font-medium">{log.error_type}</TableCell>
                    <TableCell>{log.error_code || "--"}</TableCell>
                    <TableCell className="max-w-[24rem] truncate">{log.error_message}</TableCell>
                    <TableCell>
                      {log.request_path
                        ? `${log.request_method || ""} ${log.request_path}`.trim()
                        : "--"}
                    </TableCell>
                    <TableCell>{log.created_at ? formatDate(log.created_at) : "--"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          }
        />
      </CardContent>
    </Card>
  );
}

function ExecutionsPanel({ variant }: { variant: "desktop" | "mobile" }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [taskCode, setTaskCode] = useState<string>(ALL_TASKS);
  const { data, isLoading, isFetching, isError, error, refetch } = useTaskExecutions({
    task_code: taskCode === ALL_TASKS ? undefined : taskCode,
    page,
    page_size: pageSize,
  });
  const items = data?.items ?? [];

  // 「立即清理」触发 log_cleanup 任务：#714 提交即返 + 轮询终态，终态 toast 由
  // useWatchedTaskExecution 给出，并 invalidate tasks.root 让本执行历史出新记录
  const runTask = useRunTask();
  const { track: trackExecution, runningCode } = useWatchedTaskExecution();
  const cleaning = runningCode === "log_cleanup";
  const [confirmOpen, setConfirmOpen] = useState(false);

  const handleCleanup = () => {
    runTask.mutate("log_cleanup", {
      onSuccess: (accepted) => trackExecution("log_cleanup", accepted.execution_id),
    });
    setConfirmOpen(false);
  };

  return (
    <Card data-testid="executions-card">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1.5">
          <CardTitle>任务执行历史</CardTitle>
          <CardDescription>定时与手动触发的任务执行记录</CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {cleaning && (
            <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              执行中
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            data-testid="log-cleanup-run"
            onClick={() => setConfirmOpen(true)}
            disabled={runTask.isPending || cleaning}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            立即清理
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">按任务过滤</span>
          <Select
            value={taskCode}
            onValueChange={(v) => {
              setTaskCode(v);
              setPage(1);
            }}
          >
            <SelectTrigger className="h-9 w-44" data-testid="executions-task-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_TASKS}>全部任务</SelectItem>
              {TASK_CODE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <PaginatedPanel
          variant={variant}
          isLoading={isLoading}
          isFetching={isFetching}
          isError={isError}
          error={error}
          refetch={refetch}
          isEmpty={items.length === 0}
          emptyMessage="暂无执行记录"
          total={data?.total ?? 0}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>任务</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>触发方式</TableHead>
                  <TableHead className="text-right">执行耗时</TableHead>
                  <TableHead>开始时间</TableHead>
                  <TableHead>结束时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((exec) => (
                  <TableRow key={exec.id}>
                    <TableCell className="font-medium">{exec.task_code}</TableCell>
                    <TableCell>
                      <Badge variant={getStatusBadgeVariant(exec.status)}>
                        {EXECUTION_STATUS_LABELS[exec.status] ?? exec.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {exec.trigger_type ? (
                        <Badge variant={getTriggerBadgeVariant(exec.trigger_type)}>
                          {TRIGGER_LABELS[exec.trigger_type] ?? exec.trigger_type}
                        </Badge>
                      ) : (
                        "--"
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {exec.duration_ms ? `${(exec.duration_ms / 1000).toFixed(1)}s` : "--"}
                    </TableCell>
                    <TableCell>{exec.started_at ? formatDate(exec.started_at) : "--"}</TableCell>
                    <TableCell>{exec.finished_at ? formatDate(exec.finished_at) : "--"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          }
        />
      </CardContent>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="清理过期日志"
        description="将按保留策略清理过期的登录/审计/错误日志与任务执行记录，确定继续吗？"
        confirmText="清理"
        onConfirm={handleCleanup}
      />
    </Card>
  );
}

/**
 * 日志管理（设置页内 Tab，admin-only，#715）：登录/审计/错误三类日志 + 任务执行历史。
 * 双端共用，variant 只改分页条形态与表格横向滚动。任务页下线后，执行历史与 log_cleanup
 * 手动触发的唯一 UI 入口迁到此处；任务启停退到 ir-cli。
 */
export default function LogsContent({ variant = "desktop" }: LogsContentProps) {
  return (
    <Tabs defaultValue="login" className="space-y-4">
      <TabsList>
        <TabsTrigger value="login">登录日志</TabsTrigger>
        <TabsTrigger value="audit">审计日志</TabsTrigger>
        <TabsTrigger value="error">错误日志</TabsTrigger>
        <TabsTrigger value="executions">执行历史</TabsTrigger>
      </TabsList>
      <TabsContent value="login">
        <LoginLogsPanel variant={variant} />
      </TabsContent>
      <TabsContent value="audit">
        <AuditLogsPanel variant={variant} />
      </TabsContent>
      <TabsContent value="error">
        <ErrorLogsPanel variant={variant} />
      </TabsContent>
      <TabsContent value="executions">
        <ExecutionsPanel variant={variant} />
      </TabsContent>
    </Tabs>
  );
}
