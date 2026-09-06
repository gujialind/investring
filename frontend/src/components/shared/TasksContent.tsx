"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Play, Pause, RotateCcw, Loader2 } from "lucide-react";
import { useTaskList, useTaskExecutions, useRunTask, useEnableTask, useDisableTask } from "@/hooks/useTask";
import { usePortfolioList, useUpdatePortfolio } from "@/hooks/usePortfolio";
import { formatDate, getStatusBadgeVariant } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import { useState } from "react";
import type { Portfolio } from "@/types/portfolio";

// 组合自动快照开关行（#156）：每行独立的 useUpdatePortfolio 链路，须为独立组件以合规调用 hook
function PortfolioAutoSnapshotRow({ portfolio }: { portfolio: Portfolio }) {
  const updatePortfolio = useUpdatePortfolio(portfolio.code);
  return (
    <div className="flex items-center justify-between gap-4 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <div className="text-sm font-medium">{portfolio.name}</div>
        <div className="text-xs text-muted-foreground">{portfolio.code}</div>
      </div>
      <Switch
        checked={portfolio.auto_snapshot_enabled}
        disabled={updatePortfolio.isPending}
        onCheckedChange={(next) => updatePortfolio.mutate({ auto_snapshot_enabled: next })}
        aria-label={`${portfolio.name} 自动生成快照`}
      />
    </div>
  );
}

export default function TasksContent() {
  const { data: tasksData, isLoading } = useTaskList();
  const tasks = tasksData?.items || [];

  const { data: executionsData, isLoading: execLoading } = useTaskExecutions();
  const executions = executionsData?.items || [];

  // 组合自动快照开关列表（#156）：仅列活跃组合
  const { data: portfoliosData, isLoading: portfoliosLoading } = usePortfolioList({
    status: "active",
    page_size: 100,
  });
  const portfolios = portfoliosData?.items || [];

  const runTask = useRunTask();
  const enableTask = useEnableTask();
  const disableTask = useDisableTask();
  const [pendingRunCode, setPendingRunCode] = useState<string | null>(null);

  const handleToggle = (code: string, isEnabled: boolean) => {
    if (isEnabled) {
      disableTask.mutate(code);
    } else {
      enableTask.mutate(code);
    }
  };

  const handleRun = (code: string) => {
    setPendingRunCode(code);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">任务管理</h1>
        <p className="text-muted-foreground">
          管理定时任务和手动触发
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>组合自动快照</CardTitle>
          <CardDescription>
            开启后每个交易日由定时任务自动补齐该组合快照；关闭不影响手动生成
          </CardDescription>
        </CardHeader>
        <CardContent>
          {portfoliosLoading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              加载中...
            </div>
          ) : portfolios.length === 0 ? (
            <div className="text-xs text-muted-foreground">暂无活跃组合</div>
          ) : (
            <div className="divide-y">
              {portfolios.map((p) => (
                <PortfolioAutoSnapshotRow key={p.code} portfolio={p} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>定时任务</CardTitle>
          <CardDescription>
            系统定时任务列表
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>任务名称</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>Cron表达式</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>上次执行</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tasks.map((task) => (
                <TableRow key={task.code}>
                  <TableCell className="font-medium">{task.name}</TableCell>
                  <TableCell>{task.description || "--"}</TableCell>
                  <TableCell>
                    <code className="rounded bg-muted px-2 py-1 text-sm">
                      {task.cron_expr || "--"}
                    </code>
                  </TableCell>
                  <TableCell>
                    <Badge variant={task.is_enabled ? "success" : "neutral"}>
                      {task.is_enabled ? "启用" : "禁用"}
                    </Badge>
                  </TableCell>
                  <TableCell>{task.last_run_at ? formatDate(task.last_run_at) : "从未执行"}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRun(task.code)}
                      disabled={runTask.isPending}
                    >
                      <RotateCcw className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleToggle(task.code, task.is_enabled)}
                      disabled={enableTask.isPending || disableTask.isPending}
                    >
                      {task.is_enabled ? (
                        <Pause className="h-4 w-4" />
                      ) : (
                        <Play className="h-4 w-4" />
                      )}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {tasks.length === 0 && (
            <div className="text-center text-muted-foreground py-8">
              暂无定时任务
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>执行历史</CardTitle>
          <CardDescription>
            最近的任务执行记录
          </CardDescription>
        </CardHeader>
        <CardContent>
          {execLoading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              加载中...
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>任务</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">执行耗时</TableHead>
                  <TableHead>开始时间</TableHead>
                  <TableHead>结束时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {executions.map((exec) => (
                  <TableRow key={exec.id}>
                    <TableCell className="font-medium">{exec.task_code}</TableCell>
                    <TableCell>
                      <Badge variant={getStatusBadgeVariant(exec.status)}>
                        {exec.status === "success" ? "成功" : exec.status === "failed" ? "失败" : exec.status === "partial_success" ? "部分成功" : "运行中"}
                      </Badge>
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
          )}
          {!execLoading && executions.length === 0 && (
            <div className="text-center text-muted-foreground py-8">
              暂无执行记录
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!pendingRunCode}
        onOpenChange={(open) => !open && setPendingRunCode(null)}
        title="手动执行任务"
        description={`确定要立即执行任务 ${pendingRunCode} 吗？`}
        confirmText="执行"
        onConfirm={() => {
          if (pendingRunCode) runTask.mutate(pendingRunCode);
          setPendingRunCode(null);
        }}
      />
    </div>
  );
}
