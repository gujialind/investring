"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { format, isSameDay, subYears } from "date-fns";
import type { DateRange } from "react-day-picker";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Trash2,
  X,
  XCircle,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { DatePicker } from "@/components/ui/date-picker";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import RowActionButton from "@/components/shared/RowActionButton";
import EmptyState from "@/components/shared/EmptyState";
import {
  formatCurrency,
  formatNav,
  formatSharesUnit,
  getNumberCellClass,
  getSignedReturn,
  getStatusBadgeVariant,
  cn,
} from "@/lib/utils";
import { getErrorMessage } from "@/lib/api";
import { useUIStore } from "@/stores/uiStore";
import {
  useSnapshotStatus,
  useSnapshotList,
  useGenerateSnapshot,
  useGenerateNextSnapshot,
  useCatchUpSnapshots,
  useRecalculateAsync,
  useBulkDeleteSnapshots,
  useValidateSnapshot,
  useDeleteSnapshot,
} from "@/hooks/useSnapshot";
import { useUpdatePortfolio } from "@/hooks/usePortfolio";
import { useSyncJob } from "@/hooks/useSyncJob";
import type { SnapshotValidationCheck, BulkDeleteDryRunResult } from "@/types/snapshot";

interface SnapshotsContentProps {
  /** 链接前缀：桌面 "/portfolio"，移动 "/m/portfolio" */
  basePath: string;
  variant?: "desktop" | "mobile";
}

/** 默认快照区间 = 快捷项「近1年」（#152 决策，与调仓/申赎页 #125/#126 决策⑤一致） */
function defaultSnapshotRange(): DateRange {
  return { from: subYears(new Date(), 1), to: new Date() };
}

/** 与默认区间一致（isSameDay 双端比较）→ 视为「无筛选」默认态，驱动重置按钮显隐 */
function isDefaultSnapshotRange(range: DateRange | undefined): boolean {
  if (!range?.from || !range.to) return false;
  const d = defaultSnapshotRange();
  return !!d.from && !!d.to && isSameDay(range.from, d.from) && isSameDay(range.to, d.to);
}

/**
 * 快照管理页内容（#146，桌面/移动共用）。
 * 六区块：Header / 状态概览 / 操作区 / 重算任务进度 / 历史表格 / 批量删除两段式。
 * 单日删除入口收口到表格行（仅最新日可操作，快照连续原则）。
 */
export default function SnapshotsContent({ basePath, variant = "desktop" }: SnapshotsContentProps) {
  const params = useParams();
  const code = params.code as string;
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  // 快照日期筛选（#152）：默认最近 1 年，惰性初始化避免每渲染重算；X 清空 = 不按日期过滤
  const [snapshotRange, setSnapshotRange] = useState<DateRange | undefined>(() => defaultSnapshotRange());
  const hasNonDefaultRange = snapshotRange !== undefined && !isDefaultSnapshotRange(snapshotRange);

  // 数据：区间转查询参数（闭区间）；undefined 字段 axios 不传参
  const { data: statusData } = useSnapshotStatus(code);
  const { data: listData, isLoading: listLoading } = useSnapshotList(code, {
    startDate: snapshotRange?.from ? format(snapshotRange.from, "yyyy-MM-dd") : undefined,
    endDate: snapshotRange?.to ? format(snapshotRange.to, "yyyy-MM-dd") : undefined,
  });

  // Mutations
  const generateSnapshot = useGenerateSnapshot();
  const generateNext = useGenerateNextSnapshot();
  const catchUp = useCatchUpSnapshots();
  const recalculateAsync = useRecalculateAsync();
  const bulkDelete = useBulkDeleteSnapshots();
  const validateSnapshot = useValidateSnapshot();
  const deleteSnapshot = useDeleteSnapshot();
  // 组合级自动快照开关（#156）：写走组合更新，成功后额外失效快照状态查询
  const updatePortfolio = useUpdatePortfolio(code);

  const handleToggleAutoSnapshot = (next: boolean) => {
    updatePortfolio.mutate(
      { auto_snapshot_enabled: next },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: ["snapshots"] });
        },
      }
    );
  };

  // 重算任务跟踪（终态经 useSyncJob 轮询 + 下方 effect 处理）
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const { data: job } = useSyncJob(activeJobId);

  // 追平对话框
  const [catchUpOpen, setCatchUpOpen] = useState(false);
  const [toDate, setToDate] = useState<Date>();

  // 单日生成对话框（预检两段，逻辑搬自旧页）
  const [singleOpen, setSingleOpen] = useState(false);
  const [singleDate, setSingleDate] = useState<Date>();
  const [validationResult, setValidationResult] = useState<SnapshotValidationCheck[] | null>(null);

  // 区间重算对话框
  const [recalcOpen, setRecalcOpen] = useState(false);
  const [startDate, setStartDate] = useState<Date>();
  const [endDate, setEndDate] = useState<Date>();
  const [recalcAck, setRecalcAck] = useState(false);

  // 批量删除两段式对话框
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkFromDate, setBulkFromDate] = useState<Date>();
  const [bulkPreview, setBulkPreview] = useState<BulkDeleteDryRunResult | null>(null);
  const [bulkAck, setBulkAck] = useState(false);

  // 单日删除确认（仅最新日可点）
  const [pendingDeleteDate, setPendingDeleteDate] = useState<string | null>(null);

  // 重算终态副作用：toast + 失效，每 job 每终态只触发一次（轮询末次与停轮询重渲染防双 toast）
  const jobTerminalRef = useRef<string | null>(null);
  useEffect(() => {
    if (!job || (job.status !== "success" && job.status !== "failed")) return;
    const key = `${job.id}:${job.status}`;
    if (jobTerminalRef.current === key) return;
    jobTerminalRef.current = key;
    if (job.status === "success") {
      addToast({
        type: "success",
        title: "重算完成",
        message: `共处理 ${job.total} 个交易日`,
      });
      queryClient.invalidateQueries({ queryKey: ["snapshots"] });
      queryClient.invalidateQueries({ queryKey: ["portfolios"] });
    } else {
      addToast({
        type: "error",
        title: "重算失败，已整体回滚",
        message: job.error_message ?? "请查看任务详情",
      });
    }
  }, [job, addToast, queryClient]);

  // ---- 操作处理 ----

  const handleValidateSingle = async () => {
    if (!singleDate) return;
    const result = await validateSnapshot.mutateAsync({
      portfolioCode: code,
      targetDate: format(singleDate, "yyyy-MM-dd"),
    });
    setValidationResult(result.checks);
  };

  const handleGenerateSingle = async () => {
    if (!singleDate) return;
    await generateSnapshot.mutateAsync({
      portfolioCode: code,
      targetDate: format(singleDate, "yyyy-MM-dd"),
    });
    setSingleOpen(false);
    setValidationResult(null);
  };

  const handleCatchUp = async () => {
    if (!toDate) return;
    await catchUp.mutateAsync({ portfolioCode: code, toDate: format(toDate, "yyyy-MM-dd") });
    setCatchUpOpen(false);
    setToDate(undefined);
  };

  const handleRecalculate = async () => {
    if (!startDate || !endDate) return;
    const result = await recalculateAsync.mutateAsync({
      portfolioCode: code,
      startDate: format(startDate, "yyyy-MM-dd"),
      endDate: format(endDate, "yyyy-MM-dd"),
    });
    setActiveJobId(result.job_id);
    jobTerminalRef.current = null;
    setRecalcOpen(false);
    setRecalcAck(false);
  };

  const handleBulkPreview = async () => {
    if (!bulkFromDate) return;
    const result = await bulkDelete.mutateAsync({
      portfolioCode: code,
      fromDate: format(bulkFromDate, "yyyy-MM-dd"),
      mode: "dry_run",
    });
    if ("dry_run" in result) {
      setBulkPreview(result);
    }
  };

  const handleBulkConfirm = async () => {
    if (!bulkFromDate || !bulkPreview) return;
    try {
      const result = await bulkDelete.mutateAsync({
        portfolioCode: code,
        fromDate: format(bulkFromDate, "yyyy-MM-dd"),
        mode: "confirm",
      });
      if (!("dry_run" in result)) {
        addToast({ type: "success", title: "批量删除完成", message: result.message });
      }
      setBulkOpen(false);
      setBulkPreview(null);
      setBulkAck(false);
      setBulkFromDate(undefined);
    } catch (error) {
      addToast({
        type: "error",
        title: "批量删除失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    }
  };

  const items = listData?.items ?? [];
  const jobParams = job?.params as { start_date?: string; end_date?: string } | null | undefined;
  const jobRunning = job?.status === "pending" || job?.status === "running";

  return (
    <div className={variant === "mobile" ? "space-y-4 p-4" : "container mx-auto px-4 py-6 space-y-6"}>
      {/* 1. Header */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <Link href={`${basePath}/${code}`}>
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              返回
            </Button>
          </Link>
        </div>
        <h1 className="text-2xl font-semibold">快照管理</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理组合历史快照数据，支持手动生成、追平与区间重算
        </p>
      </div>

      {/* 2. 状态概览 */}
      <Card>
        <CardHeader>
          <CardTitle>快照状态</CardTitle>
          <CardDescription>组合快照数据统计</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 border rounded-lg">
              <div className="text-xs text-muted-foreground">最新快照日期</div>
              <div className="text-lg font-semibold mt-1">
                {statusData?.latest_snapshot_date || "无"}
              </div>
            </div>
            <div className="p-4 border rounded-lg">
              <div className="text-xs text-muted-foreground">快照总数</div>
              <div className="text-lg font-semibold mt-1">
                {statusData?.total_snapshots ?? 0}
              </div>
            </div>
            <div className="p-4 border rounded-lg">
              <div className="text-xs text-muted-foreground">最早快照日期</div>
              <div className="text-lg font-semibold mt-1">
                {statusData?.first_snapshot_date || "无"}
              </div>
            </div>
          </div>

          {/* 组合级自动快照开关（#156） */}
          <div className="mt-4 flex items-center justify-between gap-4">
            <div className="space-y-0.5">
              <Label htmlFor="auto-snapshot-switch" className="text-sm font-medium">
                自动生成快照
              </Label>
              <p className="text-xs text-muted-foreground">
                开启后每个交易日由定时任务自动补齐快照；关闭不影响手动生成
              </p>
            </div>
            <Switch
              id="auto-snapshot-switch"
              checked={statusData?.auto_snapshot_enabled ?? false}
              disabled={updatePortfolio.isPending || statusData?.auto_snapshot_enabled === undefined}
              onCheckedChange={handleToggleAutoSnapshot}
            />
          </div>

          {/* 负现金平台预警（issue #71） */}
          {statusData?.negative_cash_platforms && statusData.negative_cash_platforms.length > 0 && (
            <Alert variant="destructive" className="mt-4">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                最新快照日以下平台现金为负，请排查资金流水：
                {statusData.negative_cash_platforms.join("、")}
              </AlertDescription>
            </Alert>
          )}

          {/* 缺失的交易日（#146：status.missing_dates 真值化后有真实数据） */}
          {statusData?.missing_dates && statusData.missing_dates.length > 0 && (
            <div className="mt-4">
              <div className="text-xs text-muted-foreground mb-2">
                缺失的快照日期（首末快照日区间内的交易日空洞）
              </div>
              <div className="flex flex-wrap gap-2">
                {statusData.missing_dates.slice(0, 20).map((d) => (
                  <Badge key={d} variant="destructive">
                    {d}
                  </Badge>
                ))}
                {statusData.missing_dates.length > 20 && (
                  <Badge variant="secondary">+{statusData.missing_dates.length - 20} 更多</Badge>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 3. 操作区：主操作唯一（生成下一日），危险操作（批量删除）分区放置 */}
      <Card>
        <CardHeader>
          <CardTitle>快照操作</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className={variant === "mobile" ? "grid grid-cols-2 gap-2" : "flex flex-wrap gap-2"}>
            <Button
              onClick={() => generateNext.mutate(code)}
              disabled={generateNext.isPending}
            >
              {generateNext.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  生成中…
                </>
              ) : (
                "生成下一日快照"
              )}
            </Button>
            <Button variant="outline" onClick={() => setCatchUpOpen(true)}>
              追平至日期
            </Button>
            <Button variant="outline" onClick={() => setSingleOpen(true)}>
              单日生成
            </Button>
            <Button variant="outline" onClick={() => setRecalcOpen(true)}>
              <RefreshCw className="mr-2 h-4 w-4" />
              区间重算
            </Button>
          </div>
          <div className={variant === "mobile" ? "grid grid-cols-2 gap-2" : "flex flex-wrap gap-2"}>
            <Button variant="outline" onClick={() => setBulkOpen(true)}>
              <Trash2 className="mr-2 h-4 w-4" />
              批量删除
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 4. 重算任务进度区块（规范 §14 行内区块，不引 Progress） */}
      {activeJobId != null && job && (
        <Alert>
          <div className="flex items-start gap-2 w-full">
            {jobRunning && <Loader2 className="h-4 w-4 animate-spin mt-0.5" />}
            <div className="flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <Badge variant={getStatusBadgeVariant(job.status)}>{job.status}</Badge>
                <span className="text-sm">
                  区间重算 {jobParams?.start_date ?? ""} ~ {jobParams?.end_date ?? ""}
                </span>
              </div>
              {job.status === "failed" && job.error_message && (
                <AlertDescription className="text-sm">{job.error_message}</AlertDescription>
              )}
            </div>
            {!jobRunning && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setActiveJobId(null)}
                aria-label="关闭任务状态"
              >
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>
        </Alert>
      )}

      {/* 5. 历史快照表格 */}
      <Card>
        <CardHeader>
          <CardTitle>历史快照</CardTitle>
          <CardDescription>按交易日倒序；涨跌幅按相邻交易日净值计算</CardDescription>
        </CardHeader>
        <CardContent>
          {/* 筛选行（#152）：默认最近 1 年，X 清空后为后端默认最近 500 条（与调仓页语义一致） */}
          <div
            className={
              variant === "mobile"
                ? "mb-3 space-y-2"
                : "mb-3 flex flex-wrap items-center gap-2"
            }
          >
            <DateRangePicker
              value={snapshotRange}
              onChange={setSnapshotRange}
              placeholder="快照日期"
              numberOfMonths={variant === "mobile" ? 1 : 2}
              className={variant === "mobile" ? "h-9 w-full" : "h-9 w-[240px]"}
            />
            {hasNonDefaultRange && (
              <Button
                variant="ghost"
                size="sm"
                className="h-9"
                onClick={() => setSnapshotRange(defaultSnapshotRange())}
              >
                重置
              </Button>
            )}
          </div>
          {listLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : items.length === 0 ? (
            snapshotRange ? (
              <EmptyState
                message="当前区间暂无快照"
                description="可扩大日期区间或清除筛选后查看"
                action={
                  hasNonDefaultRange ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSnapshotRange(defaultSnapshotRange())}
                    >
                      重置筛选
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <EmptyState message="暂无快照" description="可经上方操作区生成首份快照" />
            )
          ) : (
            <>
              <div className={variant === "mobile" ? "overflow-x-auto" : undefined}>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-muted-foreground">日期</TableHead>
                      <TableHead className={cn("text-muted-foreground", getNumberCellClass())}>净值</TableHead>
                      <TableHead className={cn("text-muted-foreground", getNumberCellClass())}>涨跌</TableHead>
                      <TableHead className={cn("text-muted-foreground", getNumberCellClass())}>份额</TableHead>
                      <TableHead className={cn("text-muted-foreground", getNumberCellClass())}>市值</TableHead>
                      <TableHead className={cn("text-muted-foreground", getNumberCellClass())}>在途</TableHead>
                      <TableHead className="text-muted-foreground text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {items.map((item, i) => {
                      // 涨跌 = 当日净值 / 前一交易日净值 − 1（数组倒序，i+1 即前一交易日；快照连续原则保证相邻）
                      const prev = items[i + 1];
                      const pct = prev ? (item.unit_price / prev.unit_price - 1) * 100 : null;
                      const ret = getSignedReturn(pct);
                      const isLatest = i === 0;
                      return (
                        <TableRow key={item.snapshot_date}>
                          <TableCell>{item.snapshot_date}</TableCell>
                          <TableCell className={getNumberCellClass()}>{formatNav(item.unit_price)}</TableCell>
                          <TableCell className={getNumberCellClass()}>
                            <span className={ret.colorClass}>{ret.text}</span>
                          </TableCell>
                          <TableCell className={getNumberCellClass()}>{formatSharesUnit(item.total_shares)}</TableCell>
                          <TableCell className={getNumberCellClass()}>{formatCurrency(item.total_value)}</TableCell>
                          <TableCell className={getNumberCellClass()}>{formatCurrency(item.in_transit_total)}</TableCell>
                          <TableCell className="text-right">
                            <RowActionButton
                              icon={Trash2}
                              label="删除"
                              destructive
                              disabled={!isLatest || deleteSnapshot.isPending}
                              disabledHint={isLatest ? undefined : "快照连续原则：仅可删除最新日快照"}
                              onClick={() => isLatest && setPendingDeleteDate(item.snapshot_date)}
                            />
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
              {listData && listData.total > items.length && (
                <p className="text-xs text-muted-foreground mt-2">
                  {snapshotRange
                    ? `区间内共 ${listData.total} 条，仅显示最近 ${items.length} 条`
                    : `仅显示最近 ${items.length} 条，共 ${listData.total} 条`}
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* 追平对话框：逐日 checkpoint 语义（单日失败前功保留） */}
      <Dialog open={catchUpOpen} onOpenChange={setCatchUpOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>追平快照</DialogTitle>
            <DialogDescription>
              从最新快照日的下一交易日起，逐日生成至目标日期
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>目标日期</Label>
              <DatePicker date={toDate} onSelect={setToDate} placeholder="选择日期" showTradingDays />
            </div>
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                逐日生成并逐日提交：若某一日失败，该日之前的成果保留，可修复数据后再次追平。
              </AlertDescription>
            </Alert>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCatchUpOpen(false)}>
              取消
            </Button>
            <Button onClick={handleCatchUp} disabled={!toDate || catchUp.isPending}>
              {catchUp.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  追平中…
                </>
              ) : (
                "开始追平"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 单日生成对话框（预检两段，逻辑搬自旧页） */}
      <Dialog open={singleOpen} onOpenChange={setSingleOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>生成单日快照</DialogTitle>
            <DialogDescription>选择要生成快照的日期，系统将自动校验依赖数据</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>目标日期</Label>
              <DatePicker
                date={singleDate}
                onSelect={setSingleDate}
                placeholder="选择日期"
                showTradingDays
              />
            </div>
            {validationResult && (
              <div className="space-y-2">
                <Label>校验结果</Label>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {validationResult.map((check) => (
                    <Alert
                      key={check.check_type}
                      variant={check.status === "failed" ? "destructive" : "default"}
                      className={cn(
                        check.status === "passed" && "border-success/40 bg-success-soft",
                        check.status === "warning" && "border-warning/40 bg-warning-soft"
                      )}
                    >
                      {check.status === "passed" && <CheckCircle2 className="h-4 w-4 text-success" />}
                      {check.status === "failed" && <XCircle className="h-4 w-4" />}
                      {check.status === "warning" && <AlertTriangle className="h-4 w-4 text-warning" />}
                      <AlertDescription>
                        <span className="font-medium">{check.check_type}</span>
                        <span className="ml-2">{check.message}</span>
                      </AlertDescription>
                    </Alert>
                  ))}
                </div>
              </div>
            )}
          </div>
          <DialogFooter>
            {!validationResult ? (
              <Button
                onClick={handleValidateSingle}
                disabled={!singleDate || validateSnapshot.isPending}
              >
                {validateSnapshot.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    校验中…
                  </>
                ) : (
                  "预检验证"
                )}
              </Button>
            ) : (
              <>
                <Button variant="outline" onClick={() => setValidationResult(null)}>
                  返回
                </Button>
                <Button
                  onClick={handleGenerateSingle}
                  disabled={
                    validationResult.some((c) => c.status === "failed") ||
                    generateSnapshot.isPending
                  }
                >
                  {generateSnapshot.isPending ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      生成中…
                    </>
                  ) : (
                    "确认生成"
                  )}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 区间重算对话框：异步语义（单事务，失败全回滚）+ checkbox 风险确认 */}
      <Dialog open={recalcOpen} onOpenChange={setRecalcOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>区间重算快照</DialogTitle>
            <DialogDescription>
              提交后台任务执行，页面内展示进度与终态；任一日失败整体回滚无变化
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>起始日期</Label>
              <DatePicker date={startDate} onSelect={setStartDate} placeholder="起始日期" showTradingDays />
            </div>
            <div className="space-y-2">
              <Label>结束日期</Label>
              <DatePicker date={endDate} onSelect={setEndDate} placeholder="结束日期" showTradingDays />
            </div>
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                重算将删除指定区间内的所有快照并重新生成，此操作不可撤销。
                请确保净值数据、交易数据等已完整同步。
              </AlertDescription>
            </Alert>
            <div className="flex items-center space-x-2">
              <Checkbox
                id="recalc-ack"
                checked={recalcAck}
                onCheckedChange={(checked) => setRecalcAck(checked === true)}
              />
              <Label htmlFor="recalc-ack" className="text-sm">
                我已了解重算将删除区间内全部快照并重新生成，此操作不可撤销
              </Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRecalcOpen(false)}>
              取消
            </Button>
            <Button
              onClick={handleRecalculate}
              disabled={
                !startDate ||
                !endDate ||
                startDate > endDate ||
                !recalcAck ||
                recalculateAsync.isPending
              }
              className="bg-destructive-solid text-white hover:bg-destructive-solid/90"
            >
              {recalculateAsync.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  提交中…
                </>
              ) : (
                "提交重算任务"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 批量删除两段式对话框（规范 §14：dry_run 预览 → 勾选确认 → confirm） */}
      <Dialog
        open={bulkOpen}
        onOpenChange={(open) => {
          if (bulkDelete.isPending) return; // confirm pending 期间禁止关框重复操作
          setBulkOpen(open);
          if (!open) {
            setBulkPreview(null);
            setBulkAck(false);
          }
        }}
      >
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>批量删除快照</DialogTitle>
            <DialogDescription>
              删除起始日及之后的全部快照（倒序逐日删除并级联回退）
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>起始日期</Label>
              <DatePicker
                date={bulkFromDate}
                onSelect={(d) => {
                  setBulkFromDate(d);
                  setBulkPreview(null);
                  setBulkAck(false);
                }}
                placeholder="选择起始日期"
                showTradingDays
                disabled={bulkDelete.isPending}
              />
            </div>

            {bulkPreview && bulkPreview.count === 0 && (
              <Alert>
                <AlertDescription>该日期及之后无快照可删除</AlertDescription>
              </Alert>
            )}

            {bulkPreview && bulkPreview.count > 0 && (
              <>
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    将删除 {bulkPreview.count} 张快照（
                    {bulkPreview.snapshot_dates[bulkPreview.snapshot_dates.length - 1]} ~{" "}
                    {bulkPreview.snapshot_dates[0]}
                    ），此操作不可恢复
                  </AlertDescription>
                </Alert>
                <div className="max-h-40 overflow-y-auto border rounded-md p-2">
                  <div className="flex flex-wrap gap-2">
                    {bulkPreview.snapshot_dates.map((d) => (
                      <Badge key={d} variant="secondary">
                        {d}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="bulk-ack"
                    checked={bulkAck}
                    onCheckedChange={(checked) => setBulkAck(checked === true)}
                    disabled={bulkDelete.isPending}
                  />
                  <Label htmlFor="bulk-ack" className="text-sm">
                    我已了解将删除上述快照并级联回退，此操作不可恢复
                  </Label>
                </div>
              </>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setBulkOpen(false)}
              disabled={bulkDelete.isPending}
            >
              取消
            </Button>
            {!bulkPreview || bulkPreview.count === 0 ? (
              <Button
                onClick={handleBulkPreview}
                disabled={!bulkFromDate || bulkDelete.isPending}
              >
                {bulkDelete.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    预览中…
                  </>
                ) : (
                  "预览影响"
                )}
              </Button>
            ) : (
              <Button
                onClick={handleBulkConfirm}
                disabled={!bulkAck || bulkDelete.isPending}
                className="bg-destructive-solid text-white hover:bg-destructive-solid/90"
              >
                {bulkDelete.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    删除中…
                  </>
                ) : (
                  "确认删除"
                )}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 单日删除确认（仅最新日入口可触发） */}
      <AlertDialog open={!!pendingDeleteDate} onOpenChange={(open) => !open && setPendingDeleteDate(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除快照</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除 {pendingDeleteDate} 的快照吗？删除会级联回退该日的申购确认与份额事件，此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive-solid text-white hover:bg-destructive-solid/90"
              onClick={() => {
                if (pendingDeleteDate) {
                  deleteSnapshot.mutate({ portfolioCode: code, snapshotDate: pendingDeleteDate });
                }
                setPendingDeleteDate(null);
              }}
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
