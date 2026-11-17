"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AlertCircle, Loader2 } from "lucide-react";
import { Product } from "@/types/product";
import { batchAvailableDataSources, batchDataSourceSwitchHint } from "@/lib/dataSource";
import { getErrorMessage, syncJobApi } from "@/lib/api";
import { useBatchSetDataSource } from "@/hooks/useProduct";
import { useSyncJob } from "@/hooks/useSyncJob";
import { queryKeys } from "@/lib/queryKeys";

interface BatchDataSourceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 当前页已选产品（显式清单，≤50 由列表侧钳制） */
  products: Product[];
  /** 任务到达终态时回调（列表侧据此清空选择） */
  onFinished?: () => void;
}

const TERMINAL = ["success", "partial", "failed", "interrupted"];

/**
 * 批量修改数据源对话框（#882，SPEC v1.1 V4 误操作防护）。
 * 两段式：确认段显示只数/目标源/后果/清单预览 → 提交受理 job_id → 进度段轮询
 * 终态（useSyncJob 2s）+ 失败清单（/details 台账）。提交失败 toast 透传后端中文
 * 消息、对话框不关（同 PR1 探测失败形态）；终态后失效产品列表刷新数据源列。
 * 不支持运行中取消（既有 sync_job 无 cancel 机制）；误操作恢复 = 反向批量切回。
 *
 * 会话隔离（L2 B3）：每次打开递增 session；迟到受理回执与旧 job 终态副作用只处理
 * 本 session 的 job，关闭→改选→重开不被旧任务接管、新选择不被旧终态清除。
 * 读侧失败可见（L2 B4，visual-spec §14）：status/details 查询失败显示 destructive
 * inline Alert + 后端 detail.message + 重试，不落进「提交中」或空失败清单。
 * 异常终态如实表达（L2 B5）：failed 保留已提交 checkpoint 的成功计数，仅零成功才说全部未切换。
 */
export default function BatchDataSourceDialog({
  open,
  onOpenChange,
  products,
  onFinished,
}: BatchDataSourceDialogProps) {
  const [target, setTarget] = useState<string | undefined>(undefined);
  const [jobId, setJobId] = useState<number | null>(null);
  const queryClient = useQueryClient();
  const submit = useBatchSetDataSource();

  const markets = useMemo(() => products.map((p) => p.market), [products]);
  const sourceOptions = useMemo(() => batchAvailableDataSources(markets), [markets]);
  const hint = useMemo(() => batchDataSourceSwitchHint(markets), [markets]);

  // 终态副作用去重键（open 重置时清空）
  const terminalRef = useRef<string | null>(null);
  // 会话标识：每次打开递增；受理回执与终态副作用绑定提交时的 session（L2 B3）
  const sessionRef = useRef(0);
  const jobSessionRef = useRef<number | null>(null);
  useEffect(() => {
    if (open) {
      sessionRef.current += 1;
      jobSessionRef.current = null;
      terminalRef.current = null;
      setTarget(undefined);
      setJobId(null);
    }
  }, [open]);

  const {
    data: job,
    isError: statusIsError,
    error: statusError,
    isFetching: statusFetching,
    refetch: refetchStatus,
  } = useSyncJob(jobId);
  const isTerminal = !!job && TERMINAL.includes(job.status);

  const {
    data: detailsData,
    isError: detailsIsError,
    error: detailsError,
    isFetching: detailsFetching,
    refetch: refetchDetails,
  } = useQuery({
    queryKey: ["sync-jobs", jobId, "details"],
    queryFn: () => syncJobApi.details(jobId!),
    enabled: jobId != null && isTerminal,
    retry: false,
  });
  const failures = useMemo(
    () => (detailsData?.details ?? []).filter((r) => r.status !== "success"),
    [detailsData]
  );

  // 终态副作用：刷新产品列表 + 通知列表侧清空选择，每 job 每终态只触发一次；
  // 仅本会话且弹窗开着时生效（旧 job 迟到终态不得清掉新选择，L2 B3）
  useEffect(() => {
    if (!open || !job || !isTerminal) return;
    if (jobSessionRef.current !== sessionRef.current) return;
    const key = `${job.id}:${job.status}`;
    if (terminalRef.current === key) return;
    terminalRef.current = key;
    queryClient.invalidateQueries({ queryKey: queryKeys.products.root });
    onFinished?.();
  }, [open, job, isTerminal, queryClient, onFinished]);

  const handleSubmit = () => {
    if (!target) return;
    const session = sessionRef.current;
    submit.mutate(
      {
        products: products.map((p) => ({ code: p.code, market: p.market ?? "" })),
        data_source: target,
      },
      {
        onSuccess: (accepted) => {
          // 迟到回执隔离：弹窗已重开（session 变化）则丢弃，不接管新会话（L2 B3）
          if (session !== sessionRef.current) return;
          jobSessionRef.current = session;
          terminalRef.current = null;
          setJobId(accepted.job_id);
        },
      }
    );
  };

  const statusText = !job
    ? "任务提交中…"
    : job.status === "pending" || job.status === "running"
      ? `执行中 ${job.done}/${job.total}（成功 ${job.success_count} / 跳过 ${job.skipped_count} / 失败 ${job.failed_count}）`
      : job.status === "success"
        ? `全部切换成功（${job.success_count}/${job.total}）`
        : job.status === "partial"
          ? `部分成功（成功 ${job.success_count} / 跳过 ${job.skipped_count} / 失败 ${job.failed_count}，共 ${job.total} 只）`
          : job.status === "interrupted"
            ? "任务中断（进程重启遗留），请核实台账后重试"
            : job.success_count > 0
              ? `任务异常终止（已成功 ${job.success_count} / 已处理 ${job.done} / 共 ${job.total}），成功条目已保留，请按台账核实后重试剩余`
              : `全部未切换（跳过 ${job.skipped_count} / 失败 ${job.failed_count}，共 ${job.total} 只）`;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent data-testid="batch-data-source-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {jobId == null ? "批量修改数据源" : "批量切换进度"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {jobId == null
              ? `已选 ${products.length} 只产品。提交后逐只探测目标源连通性，探测失败的产品跳过不写、其余继续；任务异步执行，期间不可取消。`
              : "任务异步执行，期间不可取消。关闭本窗不影响任务，可用 ir sync-job status/details 查询。"}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {jobId == null ? (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="batch-target-source">目标数据源</Label>
              <Select value={target ?? ""} onValueChange={setTarget}>
                <SelectTrigger id="batch-target-source" data-testid="batch-target-source">
                  <SelectValue placeholder="选择目标数据源" />
                </SelectTrigger>
                <SelectContent>
                  {sourceOptions.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {markets.includes("HK_MUTUAL") && (
                <p className="text-xs text-muted-foreground">
                  清单含港互认基金，目标源只可选 akshare（tushare 不支持港互认）。
                </p>
              )}
            </div>
            <p className="text-xs text-muted-foreground" data-testid="batch-switch-hint">
              {hint}
            </p>
            <div
              className="max-h-40 space-y-1 overflow-y-auto rounded-md border p-2 text-xs"
              data-testid="batch-preview"
            >
              {products.map((p) => (
                <div key={`${p.code}-${p.market ?? ""}`} className="flex justify-between gap-2">
                  <span>
                    {p.display_code || p.code} · {p.market || "—"}
                  </span>
                  <span className="text-muted-foreground">当前 {p.data_source ?? "—"}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-3" data-testid="batch-job-progress">
            {statusIsError ? (
              <Alert variant="destructive" data-testid="batch-status-error">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>任务状态读取失败</AlertTitle>
                <AlertDescription className="flex items-center gap-3">
                  <span className="min-w-0 break-all">
                    {getErrorMessage(statusError, "请稍后重试")}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => refetchStatus()}
                    disabled={statusFetching}
                    data-testid="batch-status-retry"
                  >
                    {statusFetching && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                    重试
                  </Button>
                </AlertDescription>
              </Alert>
            ) : (
              <div className="flex items-center gap-2 text-sm" data-testid="batch-job-status">
                {!isTerminal && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                <span>{statusText}</span>
              </div>
            )}
            {isTerminal && detailsIsError && (
              <Alert variant="destructive" data-testid="batch-details-error">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>逐产品台账读取失败</AlertTitle>
                <AlertDescription className="flex items-center gap-3">
                  <span className="min-w-0 break-all">
                    {getErrorMessage(detailsError, "请稍后重试")}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => refetchDetails()}
                    disabled={detailsFetching}
                    data-testid="batch-details-retry"
                  >
                    {detailsFetching && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                    重试
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            {isTerminal && !detailsIsError && failures.length > 0 && (
              <div
                className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-destructive/40 p-2 text-xs"
                data-testid="batch-failure-list"
              >
                {failures.map((r) => (
                  <div key={`${r.code}-${r.market}`}>
                    <span className="font-medium">
                      {r.code}（{r.market}）
                    </span>
                    <span className="text-muted-foreground">
                      {" "}
                      {r.status === "skipped" ? "跳过" : "失败"}：{r.reason ?? "未知原因"}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {isTerminal && !statusIsError && job?.error_message && (
              <p className="text-xs text-muted-foreground">{job.error_message}</p>
            )}
          </div>
        )}

        <AlertDialogFooter>
          {jobId == null ? (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={!target || submit.isPending}
                data-testid="batch-submit"
              >
                {submit.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                提交批量切换
              </Button>
            </>
          ) : (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              关闭
            </Button>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}