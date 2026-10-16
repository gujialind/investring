"use client";

import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { taskApi, getErrorMessage } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { useUIStore } from "@/stores/uiStore";
import type { TaskExecution } from "@/types/log";

// 全局执行历史 Hook（分页/过滤参数进 queryKey，换页换过滤即换键重取）
export function useTaskExecutions(params?: {
  task_code?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery({
    queryKey: queryKeys.tasks.executions(params),
    queryFn: () => taskApi.executionHistory(params),
    staleTime: 10 * 1000,
  });
}

// 手动执行任务 Hook
export function useRunTask() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (code: string) => taskApi.run(code),
    onSuccess: (_, code) => {
      // #714：请求只返回受理回执（任务此刻还没跑完），刷新让「执行中」的记录进列表
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks.root });
      addToast({ type: "success", title: "任务已启动", message: `任务 ${code} 已启动` });
    },
    onError: (error: unknown) => {
      addToast({ type: "error", title: "启动失败", message: getErrorMessage(error, "请稍后重试") });
    },
  });
}

/**
 * 跟踪一次手动触发的执行直到终态（#714）。
 *
 * 后端 `POST /{code}/run` 改为提交即返（旧形态在请求内同步跑完任务，长任务会被
 * nginx 60s 上限切断成 504、任务却在服务端照常成功），所以「成功/失败」只能在
 * 轮询到终态后说——本 hook 负责那段等待，并在此时才刷新列表。
 * 轮询节奏沿用 useSyncJob：pending/running 每 2s，终态即停。
 */
export function useWatchedTaskExecution() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);
  const [watched, setWatched] = useState<{ code: string; executionId: number } | null>(null);

  const { data: execution } = useQuery({
    queryKey: queryKeys.tasks.execution(watched?.executionId ?? null),
    queryFn: () => taskApi.getExecution(watched!.executionId),
    enabled: watched !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "running" ? 2000 : false;
    },
  });

  const isRunning =
    watched !== null && (execution?.status === "pending" || execution?.status === "running");

  useEffect(() => {
    if (!watched || !execution) return;
    if (execution.status === "pending" || execution.status === "running") return;

    const terminal = terminalToast(execution, watched.code);
    addToast(terminal);
    // 终态才刷新：last_run_at / last_run_status 与执行历史都在这一刻变
    queryClient.invalidateQueries({ queryKey: queryKeys.tasks.root });
    setWatched(null);
  }, [execution, watched, addToast, queryClient]);

  return {
    /** 提交成功后调用，开始跟踪该执行记录 */
    track: (code: string, executionId: number) => setWatched({ code, executionId }),
    /** 正在执行的任务码：用于禁用重复触发并把「执行中」显示出来 */
    // `watched &&` 只为把可空性交给 TS 收窄，语义仍由 isRunning 决定
    runningCode: watched && isRunning ? watched.code : null,
    runningExecution: watched && isRunning ? execution : null,
  };
}

function terminalToast(execution: TaskExecution, code: string) {
  const records =
    execution.records_total != null ? `，处理 ${execution.records_total} 条` : "";
  if (execution.status === "success") {
    return { type: "success" as const, title: "任务执行完成", message: `任务 ${code} 执行完成${records}` };
  }
  if (execution.status === "partial_success") {
    return {
      type: "warning" as const,
      title: "任务部分成功",
      message: execution.error_message || `任务 ${code} 部分成功${records}`,
    };
  }
  if (execution.status === "interrupted") {
    return {
      type: "warning" as const,
      title: "任务被中断",
      message: `任务 ${code} 因进程重启未完成，可重新触发`,
    };
  }
  return {
    type: "error" as const,
    title: "任务执行失败",
    message: execution.error_message || `任务 ${code} 执行失败`,
  };
}
