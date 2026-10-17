"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { subYears } from "date-fns";
import type { DateRange } from "react-day-picker";
import {
  ArrowLeft,
  ArrowLeftRight,
  Filter,
  List,
  Loader2,
  Pencil,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DateRangePicker } from "@/components/ui/date-range-picker";

import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import PaginationBar from "@/components/shared/PaginationBar";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import CashTransferDialog from "@/components/shared/dialogs/CashTransferDialog";
import CashTransferListDialog from "@/components/shared/dialogs/CashTransferListDialog";
import CashMarketValueUpdateDialog from "@/components/shared/dialogs/CashMarketValueUpdateDialog";

import { cn, formatNumber, formatSignedCurrency, toDateOnly } from "@/lib/utils";
import { getErrorMessage, type CashFlowListParams } from "@/lib/api";
import type { CashFlowRow, CashFlowStatusFilter, CashFlowType } from "@/types/cash-flow";
import {
  amountNote,
  balanceSpans,
  cashFlowAmountText,
  cashFlowDotColor,
  CASH_FLOW_STATUS_OPTIONS,
  CASH_FLOW_TYPE_OPTIONS,
  formatBalanceCurrency,
  formatBareSigned,
  normalizeMoneyText,
  projectedBalance,
  sameDayOthersCount,
  toAmount,
} from "@/lib/cashFlows";
import { useCashFlowDay, useCashFlowList } from "@/hooks/useCashFlow";
import { usePlatformList } from "@/hooks/usePlatform";
import { useConfirmCashTransfer } from "@/hooks/useCashTransfer";
import { useDeleteCashOverride } from "@/hooks/usePosition";

interface CashFlowsContentProps {
  /** 链接前缀：桌面 ""，移动 "/m"（拼 `/portfolio/{code}`） */
  basePath: "" | "/m";
  variant?: "desktop" | "mobile";
}

/** 默认区间：下界近 1 年、上界**开放**（to=undefined）——否则预计到账日在未来的
 *  在途行会被默认视图整批藏掉（mockup 决策「区间上界默认开放」）。 */
function defaultRange(): DateRange {
  return { from: subYears(new Date(), 1), to: undefined };
}

function isDefaultRange(range: DateRange | undefined): boolean {
  return (
    !!range?.from &&
    range.to === undefined &&
    toDateOnly(range.from) === toDateOnly(subYears(new Date(), 1))
  );
}

/** 行唯一键：一笔现金变动 = 一条账本来源记录（trade CASH 腿 / 事件 / mmv），origin_ref 唯一 */
function rowKey(row: CashFlowRow): string {
  return `${row.origin_ref.kind}#${row.origin_ref.id}`;
}

/**
 * 现金流水页内容（桌面/移动共用，issue #725）。
 *
 * 数据全部来自 PR-1 端点 `GET /portfolios/{code}/cash-flows`（列表 + overview 四卡 +
 * platform_breakdown 一次到位）；方向标签一律消费后端 `flow_type_label`，前端不重算方向
 * （mockup 的申赎方向是错的，见 docs/design/cash-flow-module.md）。余额列与「日期·平台」
 * 组列按 (平台, 现金日期) rowSpan 合并；「当日另有 N 笔」与详情对账块经 `useCashFlowDay`
 * 取同 (平台, 日) 全组行（解除类型筛选），故在类型筛选态下也能自证日终余额。
 */
export default function CashFlowsContent({ basePath, variant = "desktop" }: CashFlowsContentProps) {
  const params = useParams();
  const code = params.code as string;
  const isMobile = variant === "mobile";

  // 筛选状态（visual-spec §9）：空字段 undefined，axios 不传参
  const [range, setRange] = useState<DateRange | undefined>(() => defaultRange());
  const [statusFilter, setStatusFilter] = useState<CashFlowStatusFilter | undefined>(undefined);
  const [platformFilter, setPlatformFilter] = useState<string | undefined>(undefined);
  const [flowTypeFilter, setFlowTypeFilter] = useState<CashFlowType | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filterOpen, setFilterOpen] = useState(false);

  // 页头三入口（复用既有现金弹窗）+ 构成弹窗 + 行级详情/当日弹窗
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferListOpen, setTransferListOpen] = useState(false);
  const [marketValueOpen, setMarketValueOpen] = useState(false);
  const [marketValuePlatform, setMarketValuePlatform] = useState<string | undefined>(undefined);
  const [breakdownOpen, setBreakdownOpen] = useState(false);
  const [detailRow, setDetailRow] = useState<CashFlowRow | null>(null);
  const [sameDayRow, setSameDayRow] = useState<CashFlowRow | null>(null);
  // 撤销重估二次确认目标（危险操作，visual-spec §13）
  const [revokeRow, setRevokeRow] = useState<CashFlowRow | null>(null);

  const listParams: CashFlowListParams = {
    from_date: range?.from ? toDateOnly(range.from) : undefined,
    to_date: range?.to ? toDateOnly(range.to) : undefined,
    status: statusFilter,
    platform_code: platformFilter,
    flow_type: flowTypeFilter,
    page,
    page_size: pageSize,
  };

  const { data, isLoading, isFetching, isError, error, refetch } = useCashFlowList(code, listParams);
  const { data: platformsData } = usePlatformList({ page_size: 100 });

  // 详情 / 当日弹窗打开时取同 (平台, 现金日期) 全组行（不带类型/状态筛选）。
  // 对账块与「共 N 笔」计数一律消费**未过滤**的 dayGroupRows（#727 Blocker 2）：
  // useCashFlowDay 本就不带 status，若再按页内状态筛选二次过滤，筛「在途」时打开
  // pending 行详情会把同日已生效变动从对账块里筛掉，日终余额失去来源、自我否证。
  const dialogRow = detailRow ?? sameDayRow;
  const {
    data: dayData,
    isError: dayIsError,
    error: dayErr,
    refetch: refetchDay,
  } = useCashFlowDay(
    code,
    dialogRow?.platform_code ?? null,
    dialogRow?.cash_date ?? null,
    !!dialogRow
  );

  const confirmTransfer = useConfirmCashTransfer(code);
  const deleteOverride = useDeleteCashOverride(code);

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;
  const overview = data?.overview;
  const breakdown = data?.platform_breakdown;
  const platforms = useMemo(() => platformsData?.items ?? [], [platformsData]);
  const platformNameMap = useMemo(
    () => new Map(platforms.map((p) => [p.code, p.name])),
    [platforms]
  );
  const pname = (c: string | null | undefined) =>
    c ? (platformNameMap.get(c) ?? c) : "--";

  const hasNonDefaultFilter =
    statusFilter !== undefined ||
    platformFilter !== undefined ||
    flowTypeFilter !== undefined ||
    !isDefaultRange(range);
  const activeFilterCount =
    (statusFilter ? 1 : 0) +
    (platformFilter ? 1 : 0) +
    (flowTypeFilter ? 1 : 0) +
    (isDefaultRange(range) ? 0 : 1);

  const resetFilters = () => {
    setStatusFilter(undefined);
    setPlatformFilter(undefined);
    setFlowTypeFilter(undefined);
    setRange(defaultRange());
    setPage(1);
  };

  // 撤销/删除使 total 收敛到更少的页时自动回到最后一页（#727 N1）：
  // 否则停在越界页码，屏幕落「暂无现金流水」且分页条无可点页码、无法恢复。
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const spans = balanceSpans(rows);

  // ---- 概览四卡文案（存量卡只随平台筛选切 scope；流量卡随全部筛选）----
  const today = toDateOnly(new Date());
  const stockScope = platformFilter ? pname(platformFilter) : "组合级";
  const flowScope = flowTypeFilter || statusFilter ? "当前筛选" : stockScope;
  const flowNote = isMobile
    ? `仅已到账 · ${platformFilter ? "含转移" : "不含转移"}`
    : `仅已到账 · ${platformFilter ? "含转移/不含重估" : "不含转移/重估"}`;

  const statCards = overview
    ? [
        {
          key: "available",
          label: "可用现金",
          value: formatBalanceCurrency(overview.available),
          note: isMobile ? `${stockScope} · 截至 ${today}` : `${stockScope} · 截至今日（不随筛选变）`,
          warn: false,
        },
        {
          key: "in_transit",
          label: "在途资金",
          value: formatBalanceCurrency(overview.in_transit),
          note: isMobile ? "不计可用现金" : "按到账平台归属 · 不计可用现金",
          warn: true,
        },
        {
          key: "inflow",
          label: "本期流入",
          value: formatSignedCurrency(overview.inflow),
          note: `${flowScope} · ${flowNote}`,
          warn: false,
        },
        {
          key: "outflow",
          label: "本期流出",
          value: formatSignedCurrency(overview.outflow),
          note: `${flowScope} · ${flowNote}`,
          warn: false,
        },
      ]
    : [];

  // ---- 行操作 ----
  const openDetail = (row: CashFlowRow) => setDetailRow(row);
  const openSameDay = (row: CashFlowRow) => setSameDayRow(row);

  const handleConfirm = (row: CashFlowRow) => {
    const group = row.origin_ref.transfer_group;
    if (!group) return;
    // 读侧刷新由 useConfirmCashTransfer 的 onSuccess 统一失效 cashFlows.root（#727 B1）
    confirmTransfer.mutate(group);
  };
  const handleRevoke = () => {
    if (!revokeRow) return;
    deleteOverride.mutate(
      { platformCode: revokeRow.platform_code, updateDate: revokeRow.cash_date },
      { onSuccess: () => setRevokeRow(null) }
    );
  };

  const renderAction = (row: CashFlowRow) => {
    if (row.action.kind === "confirm") {
      return (
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="link"
            size="sm"
            data-testid="row-detail"
            onClick={() => openDetail(row)}
          >
            详情
          </Button>
          <Button
            variant="outline"
            size="sm"
            data-testid="row-action"
            disabled={confirmTransfer.isPending}
            onClick={() => handleConfirm(row)}
          >
            {confirmTransfer.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            {row.action.label}
          </Button>
        </div>
      );
    }
    if (row.action.kind === "revoke") {
      return (
        <Button variant="link" size="sm" data-testid="row-action" onClick={() => setRevokeRow(row)}>
          {row.action.label}
        </Button>
      );
    }
    return (
      <Button variant="link" size="sm" data-testid="row-action" onClick={() => openDetail(row)}>
        {row.action.label}
      </Button>
    );
  };

  const typeChip = (row: CashFlowRow) => (
    <Badge variant="neutral">
      <span
        className="mr-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ background: cashFlowDotColor(row) }}
      />
      {row.flow_type_label}
    </Badge>
  );

  // ---- 对账块（详情 / 当日弹窗共用）：前日终 → 当日已生效 → 日终 ----
  const renderRecon = (origin: CashFlowRow, groupRows: CashFlowRow[]) => {
    const effects = groupRows.filter((r) => r.settled && !r.is_override);
    const hasOverride = groupRows.some((r) => r.settled && r.is_override);
    const prev = origin.prev_day_balance;
    const end = origin.platform_end_of_day_balance;
    return (
      <div className="rounded-md border bg-muted/30 p-3 text-sm" data-testid="recon-block">
        <div className="mb-2 font-medium">
          {pname(origin.platform_code)} · {origin.cash_date} 日终余额对账
        </div>
        <div className="flex items-center justify-between py-0.5">
          <span className="text-muted-foreground">
            {prev === null || prev === undefined ? "期初（区间下界前）" : "前一交易日日终"}
          </span>
          <span className="number-cell">{formatBalanceCurrency(prev)}</span>
        </div>
        {effects.length === 0 && !hasOverride ? (
          <div className="flex items-center justify-between py-0.5 text-muted-foreground">
            <span>当日无已生效变动{origin.settled ? "" : "（本笔未到账，不计入）"}</span>
            <span className="number-cell">--</span>
          </div>
        ) : (
          effects.map((e) => (
            <div className="flex items-center justify-between py-0.5" key={rowKey(e)}>
              <span className="text-muted-foreground">当日已生效 · {e.flow_type_label}</span>
              <span className="number-cell">{formatBareSigned(e.amount)}</span>
            </div>
          ))
        )}
        {hasOverride && (
          <div className="py-0.5 text-xs text-muted-foreground">
            同日现金重估（绝对值覆盖）吸收当日增量，日终以重估值为准。
          </div>
        )}
        <div className="mt-1 flex items-center justify-between border-t pt-1 font-medium">
          <span>日终余额</span>
          <span className="number-cell">{formatBalanceCurrency(end)}</span>
        </div>
      </div>
    );
  };

  // 同日全组行（未过滤，对账块与「共 N 笔」的唯一数据源）与当日弹窗表格行集
  // （解除类型筛选、保留当前状态筛选——表格自身维持筛选行为，mockup `sameDayRows` 口径）
  const dayGroupRows = useMemo(() => dayData?.items ?? [], [dayData?.items]);
  const dayRows = useMemo(() => {
    if (!statusFilter) return dayGroupRows;
    return dayGroupRows.filter((r) => (statusFilter === "settled" ? r.settled : !r.settled));
  }, [dayGroupRows, statusFilter]);

  // 弹窗内同日查询的加载 / 失败态（#727 S2，visual-spec §14 Dialog 形态：失败走
  // destructive inline Alert + 重试）。不得在查询未落地时用单行兜底冒充全组对账。
  const renderDayQueryState = (): ReactNode | null => {
    if (dayIsError && !dayData) {
      return (
        <div
          className="rounded-md border border-destructive/30 bg-destructive-soft p-3"
          data-testid="day-query-error"
        >
          <p className="text-sm text-destructive-foreground">
            同日流水加载失败：{getErrorMessage(dayErr, "请稍后重试")}
          </p>
          <Button type="button" variant="link" size="sm" onClick={() => refetchDay()}>
            重试
          </Button>
        </div>
      );
    }
    if (!dayData) {
      return (
        <div
          className="flex items-center justify-center gap-2 rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground"
          aria-busy="true"
          data-testid="day-query-loading"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          同日流水加载中…
        </div>
      );
    }
    return null;
  };

  // ---- 筛选栏控件（visual-spec §9：时间 → 状态 → 实体 → 类型）----
  const rangeWidth = isMobile ? "h-9 w-full" : "h-9 w-[240px]";
  const selectWidth = isMobile ? "h-9 w-full" : "h-9 w-[160px]";
  const filterControls = (
    <>
      <DateRangePicker
        value={range}
        onChange={(r) => {
          setRange(r);
          setPage(1);
        }}
        placeholder="现金日期"
        numberOfMonths={isMobile ? 1 : 2}
        className={rangeWidth}
      />
      <Select
        value={statusFilter ?? "all"}
        onValueChange={(v) => {
          setStatusFilter(v === "all" ? undefined : (v as CashFlowStatusFilter));
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部状态" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部状态</SelectItem>
          {CASH_FLOW_STATUS_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <SearchablePlatformSelect
        platforms={platforms}
        value={platformFilter ?? null}
        onChange={(v) => {
          setPlatformFilter(v ?? undefined);
          setPage(1);
        }}
        specialOptionLabel="全部平台"
        className={selectWidth}
      />
      <Select
        value={flowTypeFilter ?? "all"}
        onValueChange={(v) => {
          setFlowTypeFilter(v === "all" ? undefined : (v as CashFlowType));
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部流水类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部流水类型</SelectItem>
          {CASH_FLOW_TYPE_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {hasNonDefaultFilter && (
        <Button variant="ghost" size="sm" className="h-9" onClick={resetFilters}>
          重置
        </Button>
      )}
    </>
  );

  const entryButtons = (
    <>
      <Button
        size={isMobile ? "sm" : "default"}
        data-testid="btn-transfer"
        className={cn(isMobile && "flex-1")}
        onClick={() => setTransferOpen(true)}
      >
        <ArrowLeftRight className="mr-2 h-4 w-4" />
        现金转移
      </Button>
      <Button
        variant="outline"
        size={isMobile ? "sm" : "default"}
        data-testid="btn-transfer-list"
        className={cn(isMobile && "flex-1")}
        onClick={() => setTransferListOpen(true)}
      >
        {isMobile ? <List className="mr-2 h-4 w-4" /> : null}
        转移记录
      </Button>
      <Button
        variant="outline"
        size={isMobile ? "sm" : "default"}
        data-testid="btn-market-value"
        className={cn(isMobile && "flex-1")}
        onClick={() => {
          setMarketValuePlatform(undefined);
          setMarketValueOpen(true);
        }}
      >
        {isMobile ? <Pencil className="mr-2 h-4 w-4" /> : null}
        {isMobile ? "更新市值" : "更新现金市值"}
      </Button>
    </>
  );

  if (isLoading) return <LoadingState />;

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center gap-4">
        <Link href={`${basePath}/portfolio/${code}`}>
          <Button variant="ghost" size="sm" aria-label="返回组合详情">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-2xl font-semibold">现金流水</h1>
          <p className="text-muted-foreground">
            组合代码: {code} ｜ 现金按平台分账，本页聚合全部现金来源
          </p>
        </div>
      </div>

      {/* 概览四卡 */}
      <Card>
        <CardContent className="pt-6">
          <div className="mb-3 flex justify-end">
            <Button variant="outline" size="sm" data-testid="open-breakdown" onClick={() => setBreakdownOpen(true)}>
              按平台构成
            </Button>
          </div>
          <div className={cn("grid gap-4", isMobile ? "grid-cols-2" : "grid-cols-2 lg:grid-cols-4")}>
            {statCards.map((s) => (
              <div key={s.key} className="flex flex-col gap-1" data-testid={`stat-${s.key}`}>
                <span className="text-sm text-muted-foreground">{s.label}</span>
                {/* 移动端两列栅格下 text-2xl 的带符号大额会溢出压到邻卡、吞掉负号 */}
                <span className={cn("number-cell font-semibold", isMobile ? "text-xl" : "text-2xl")}>
                  {s.value}
                </span>
                <span className={cn("text-xs", s.warn ? "text-warning-foreground" : "text-muted-foreground")}>
                  {s.note}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 明细卡 */}
      <Card>
        <CardHeader className={cn(isMobile ? "flex-row items-center justify-between space-y-0" : "flex-row items-start justify-between space-y-0")}>
          <div>
            <CardTitle>{isMobile ? "明细" : "现金流水明细"}</CardTitle>
            {!isMobile && (
              <CardDescription>
                调仓配对现金腿 · 申赎确认 · 现金分红 · 平台间转移 · 手动重估
              </CardDescription>
            )}
          </div>
          {isMobile ? (
            <Button variant="outline" size="sm" className="h-9" onClick={() => setFilterOpen((v) => !v)}>
              <Filter className="mr-2 h-4 w-4" />
              筛选
              {activeFilterCount > 0 && (
                <Badge variant="outline" className="ml-2 px-1.5 py-0">
                  {activeFilterCount}
                </Badge>
              )}
            </Button>
          ) : (
            <div className="flex gap-2">{entryButtons}</div>
          )}
        </CardHeader>
        <CardContent>
          {isMobile && <div className="mb-3 flex gap-2">{entryButtons}</div>}
          {/* 筛选栏（§9）：桌面平铺，移动折叠面板 + 激活计数 Badge */}
          {isMobile ? (
            filterOpen && <div className="mb-3 grid grid-cols-1 gap-2">{filterControls}</div>
          ) : (
            <div className="mb-3 flex flex-wrap items-center gap-2">{filterControls}</div>
          )}

          {/* §14：筛选/翻页局部刷新保留旧数据，半透明 + 右上角 spinner + aria-busy
              （#683 D-1 同形态；首屏在途走上方 LoadingState 提前返回，不在此列） */}
          <div className={cn("relative", isFetching && "opacity-50")} aria-busy={isFetching}>
            {isFetching && (
              <Loader2 className="absolute right-2 top-2 z-10 h-4 w-4 animate-spin text-muted-foreground" />
            )}
            {isMobile ? (
              <div className="space-y-3" data-testid="cash-flow-cards">
                {rows.map((row) => {
                  const others = sameDayOthersCount(row, rows);
                  const note = amountNote(row);
                  return (
                    <div key={rowKey(row)} className="rounded-lg border p-3" data-testid="cash-flow-row">
                      <div className="flex items-center justify-between">
                        {typeChip(row)}
                        <span className="text-xs text-muted-foreground">{row.cash_date}</span>
                      </div>
                      <div className="mt-2">
                        <div className="text-sm font-medium">{row.source_main}</div>
                        <div className="text-xs text-muted-foreground">
                          {normalizeMoneyText(row.source_sub)} · {pname(row.platform_code)}
                        </div>
                      </div>
                      <div className="mt-2 flex items-center justify-between">
                        <Badge variant={row.status.variant}>{row.status.label}</Badge>
                        <span className="number-cell font-medium">{cashFlowAmountText(row)}</span>
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        日终余额 · {pname(row.platform_code)}{" "}
                        {formatBalanceCurrency(row.platform_end_of_day_balance)}
                        {note ? ` ｜ ${note}` : ""}
                        {row.status.note ? ` ｜ ${row.status.note}` : ""}
                      </div>
                      <div className="mt-2 flex items-center gap-2">
                        {others > 0 && (
                          <Button
                            variant="link"
                            size="sm"
                            className="mr-auto h-auto p-0"
                            data-testid="same-day-link"
                            onClick={() => openSameDay(row)}
                          >
                            当日该平台另有 {others} 笔
                          </Button>
                        )}
                        {renderAction(row)}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <Table data-testid="cash-flow-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>日期 · 平台</TableHead>
                    <TableHead>流水类型</TableHead>
                    <TableHead>来源 / 去向</TableHead>
                    <TableHead className="number-cell">金额</TableHead>
                    <TableHead className="number-cell">平台日终余额</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row, i) => {
                    const others = sameDayOthersCount(row, rows);
                    const note = amountNote(row);
                    const merged = spans[i] === -1;
                    return (
                      <TableRow key={rowKey(row)} data-testid="cash-flow-row">
                        {!merged && (
                          <TableCell rowSpan={spans[i]} className="align-top" data-testid="group-cell">
                            <div className="text-sm">{row.cash_date}</div>
                            <div className="whitespace-nowrap text-xs text-muted-foreground">
                              {pname(row.platform_code)}
                            </div>
                            {others > 0 && (
                              <Button
                                variant="link"
                                size="sm"
                                className="mt-0.5 h-auto p-0 text-xs"
                                data-testid="same-day-link"
                                onClick={() => openSameDay(row)}
                              >
                                当日另有 {others} 笔
                              </Button>
                            )}
                          </TableCell>
                        )}
                        <TableCell className="align-top">
                          {typeChip(row)}
                          <div className="mt-0.5 whitespace-nowrap text-xs text-muted-foreground">
                            {row.cash_date_label}
                          </div>
                        </TableCell>
                        <TableCell className="align-top">
                          <div className="text-sm font-medium">{row.source_main}</div>
                          <div className="text-xs text-muted-foreground">
                            {normalizeMoneyText(row.source_sub)}
                          </div>
                        </TableCell>
                        <TableCell className="number-cell align-top">
                          {cashFlowAmountText(row)}
                          {note && <div className="text-xs font-normal text-muted-foreground">{note}</div>}
                        </TableCell>
                        {!merged && (
                          <TableCell
                            className="number-cell align-top"
                            rowSpan={spans[i]}
                            data-testid="balance-cell"
                          >
                            {formatBalanceCurrency(row.platform_end_of_day_balance)}
                          </TableCell>
                        )}
                        <TableCell className="align-top">
                          <Badge variant={row.status.variant}>{row.status.label}</Badge>
                          {row.status.note && (
                            <div className="mt-0.5 whitespace-nowrap text-xs text-muted-foreground">
                              {row.status.note}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="text-right align-top">{renderAction(row)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </div>

          {/* #683C 判序：失败 → 有筛选空 → 无筛选空，失败不并入 EmptyState */}
          {isError ? (
            <QueryErrorState error={error} onRetry={refetch} />
          ) : (
            rows.length === 0 &&
            (hasNonDefaultFilter ? (
              <EmptyState
                message="无符合筛选条件的记录"
                action={
                  <Button variant="ghost" size="sm" onClick={resetFilters}>
                    重置筛选
                  </Button>
                }
              />
            ) : (
              <EmptyState message="暂无现金流水" />
            ))
          )}
          <PaginationBar
            page={page}
            pageSize={pageSize}
            total={total}
            variant={variant}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        </CardContent>
      </Card>

      {/* 页头三入口弹窗（复用既有组件） */}
      <CashTransferDialog portfolioCode={code} open={transferOpen} onOpenChange={setTransferOpen} />
      <CashTransferListDialog portfolioCode={code} open={transferListOpen} onOpenChange={setTransferListOpen} />
      <CashMarketValueUpdateDialog
        portfolioCode={code}
        open={marketValueOpen}
        onOpenChange={setMarketValueOpen}
        defaultPlatformCode={marketValuePlatform}
      />

      {/* 流水详情弹窗 */}
      <Dialog open={!!detailRow} onOpenChange={(o) => !o && setDetailRow(null)}>
        <DialogContent className="sm:max-w-[560px]" data-testid="detail-dialog">
          {detailRow && (
            <>
              <DialogHeader>
                <DialogTitle>流水详情</DialogTitle>
                <DialogDescription>
                  {detailRow.flow_type_label} · {detailRow.cash_date} · {pname(detailRow.platform_code)}
                </DialogDescription>
              </DialogHeader>
              <div className="divide-y text-sm">
                <DetailRow k="现金日期">
                  <span>
                    {detailRow.cash_date}（{detailRow.cash_date_label}）
                  </span>
                  {detailRow.cash_date_sub && (
                    <div className="text-xs text-muted-foreground">{detailRow.cash_date_sub}</div>
                  )}
                </DetailRow>
                <DetailRow k="来源 / 去向">
                  <div className="font-medium">{detailRow.source_main}</div>
                  <div className="text-xs text-muted-foreground">
                    {normalizeMoneyText(detailRow.source_sub)}
                  </div>
                </DetailRow>
                <DetailRow k="金额">
                  <span className="number-cell">{cashFlowAmountText(detailRow)}</span>
                  {amountNote(detailRow) && (
                    <div className="text-xs text-muted-foreground">{amountNote(detailRow)}</div>
                  )}
                </DetailRow>
                <DetailRow k="状态">
                  <Badge variant={detailRow.status.variant}>{detailRow.status.label}</Badge>
                  {detailRow.status.note && (
                    <div className="text-xs text-muted-foreground">{detailRow.status.note}</div>
                  )}
                </DetailRow>
                {detailRow.pair_ref && (
                  <DetailRow k="关联业务">
                    <div>{detailRow.pair_ref.label}</div>
                    <div className="text-xs text-muted-foreground">
                      {detailRow.pair_ref.platform_name ?? pname(detailRow.pair_ref.platform_code)}
                      {detailRow.pair_ref.ref_date ? ` · ${detailRow.pair_ref.ref_date}` : ""}
                      {detailRow.pair_ref.amount != null
                        ? ` · ${formatSignedCurrency(detailRow.pair_ref.amount)}`
                        : ""}
                      {detailRow.pair_ref.settled != null
                        ? ` · ${detailRow.pair_ref.settled ? "已生效" : "在途"}`
                        : ""}
                    </div>
                  </DetailRow>
                )}
                <DetailRow k="数据来源">
                  <span className="font-mono text-xs">
                    {detailRow.origin_ref.kind} #{detailRow.origin_ref.id}
                    {detailRow.origin_ref.transfer_group ? ` · ${detailRow.origin_ref.transfer_group}` : ""}
                  </span>
                </DetailRow>
              </div>
              {renderDayQueryState() ?? renderRecon(detailRow, dayGroupRows)}
              {(() => {
                const proj = projectedBalance(detailRow);
                if (proj === null) return null;
                return (
                  <div className="rounded-md border border-warning bg-warning-soft p-3 text-sm" data-testid="projection-block">
                    <div className="mb-2 font-medium text-warning-foreground">
                      预计到账后余额 · {pname(detailRow.platform_code)} · {detailRow.cash_date}（预计值）
                    </div>
                    <div className="flex items-center justify-between py-0.5">
                      <span className="text-muted-foreground">当前日终余额（不含本笔）</span>
                      <span className="number-cell">{formatBalanceCurrency(detailRow.platform_end_of_day_balance)}</span>
                    </div>
                    <div className="flex items-center justify-between py-0.5">
                      <span className="text-muted-foreground">本笔到账</span>
                      <span className="number-cell">{formatBareSigned(detailRow.amount)}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between border-t border-warning/30 pt-1 font-medium">
                      <span>合计</span>
                      <span className="number-cell">{formatBalanceCurrency(proj)}</span>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      由「当前日终余额 + 本笔金额」推得，<b>不是</b>后端 calculate_available_cash 的结果；实际到账日与金额以平台为准。
                    </p>
                  </div>
                );
              })()}
              <DialogFooter>
                <Button variant="outline" onClick={() => setDetailRow(null)}>
                  关闭
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* 当日该平台全部现金流水弹窗 */}
      <Dialog open={!!sameDayRow} onOpenChange={(o) => !o && setSameDayRow(null)}>
        <DialogContent className="sm:max-w-[640px]" data-testid="sameday-dialog">
          {sameDayRow && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {pname(sameDayRow.platform_code)} · {sameDayRow.cash_date} 现金流水
                </DialogTitle>
                <DialogDescription>
                  {dayData
                    ? `当日该平台共 ${dayGroupRows.length} 笔（含被当前筛选隐藏的行）。关闭后回到当前筛选视图，筛选条件不变。`
                    : "同日流水加载中，笔数以全组数据落地为准。"}
                </DialogDescription>
              </DialogHeader>
              {renderDayQueryState() ?? (
                <>
                  {isMobile ? (
                    <div className="space-y-2">
                      {dayRows.map((r) => (
                        <div key={rowKey(r)} className="rounded-lg border p-3">
                          <div className="flex items-center justify-between">
                            {typeChip(r)}
                            {rowKey(r) === rowKey(sameDayRow) && (
                              <span className="text-xs text-muted-foreground">当前筛选命中</span>
                            )}
                          </div>
                          <div className="mt-1 text-xs text-muted-foreground">{r.source_main}</div>
                          <div className="mt-1 flex items-center justify-between">
                            <Badge variant={r.status.variant}>{r.status.label}</Badge>
                            <span className="number-cell text-sm">{cashFlowAmountText(r)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>流水类型</TableHead>
                          <TableHead>来源 / 去向</TableHead>
                          <TableHead className="number-cell">金额</TableHead>
                          <TableHead>状态</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {dayRows.map((r) => (
                          <TableRow key={rowKey(r)}>
                            <TableCell>
                              {typeChip(r)}
                              {rowKey(r) === rowKey(sameDayRow) && (
                                <div className="mt-0.5 text-xs text-muted-foreground">当前筛选命中</div>
                              )}
                            </TableCell>
                            <TableCell>
                              <div className="text-sm font-medium">{r.source_main}</div>
                              <div className="text-xs text-muted-foreground">{normalizeMoneyText(r.source_sub)}</div>
                            </TableCell>
                            <TableCell className="number-cell">
                              {cashFlowAmountText(r)}
                              {!r.settled && (
                                <div className="text-xs font-normal text-muted-foreground">
                                  {amountNote(r) ?? "未到账"}
                                </div>
                              )}
                            </TableCell>
                            <TableCell>
                              <Badge variant={r.status.variant}>{r.status.label}</Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                  {renderRecon(sameDayRow, dayGroupRows)}
                  <p className="text-xs text-muted-foreground">
                    这就是列表里那一格合并余额的来历；未到账的腿不在其中。
                  </p>
                </>
              )}
              <DialogFooter>
                <Button variant="outline" onClick={() => setSameDayRow(null)}>
                  关闭
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* 按平台构成弹窗 */}
      <Dialog open={breakdownOpen} onOpenChange={setBreakdownOpen}>
        <DialogContent className="sm:max-w-[760px]" data-testid="breakdown-dialog">
          <DialogHeader>
            <DialogTitle>按平台现金构成</DialogTitle>
            <DialogDescription>
              每行 = 页头四卡在「筛选该平台」下会显示的值；合计行 = 组合口径（无平台筛选时与四卡一致）。「看明细」应用该平台筛选并关闭弹窗；「更新」以该平台预选启动「更新现金市值」。
            </DialogDescription>
          </DialogHeader>
          {breakdown && (
            <>
              {isMobile ? (
                <div className="space-y-2">
                  {breakdown.rows.map((r) => (
                    <BreakdownCard
                      key={r.platform_code}
                      name={pname(r.platform_code)}
                      available={r.available}
                      inTransit={r.in_transit}
                      inflow={r.inflow}
                      outflow={r.outflow}
                      hit={platformFilter === r.platform_code}
                      onDrill={() => {
                        setPlatformFilter(r.platform_code);
                        setPage(1);
                        setBreakdownOpen(false);
                      }}
                      onUpdate={() => {
                        setMarketValuePlatform(r.platform_code);
                        setBreakdownOpen(false);
                        setMarketValueOpen(true);
                      }}
                    />
                  ))}
                  <BreakdownCard
                    name="合计 · 组合口径"
                    available={breakdown.total.available}
                    inTransit={breakdown.total.in_transit}
                    inflow={breakdown.total.inflow}
                    outflow={breakdown.total.outflow}
                  />
                </div>
              ) : (
                <Table data-testid="breakdown-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead>平台</TableHead>
                      <TableHead className="number-cell">可用现金</TableHead>
                      <TableHead className="number-cell">在途资金</TableHead>
                      <TableHead className="number-cell">本期流入</TableHead>
                      <TableHead className="number-cell">本期流出</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {breakdown.rows.map((r) => (
                      <TableRow key={r.platform_code} data-testid="breakdown-row">
                        <TableCell>
                          <div className="whitespace-nowrap text-sm">{pname(r.platform_code)}</div>
                          {platformFilter === r.platform_code && (
                            <div className="text-xs text-muted-foreground">当前筛选</div>
                          )}
                        </TableCell>
                        <TableCell className="number-cell">{formatBalanceCurrency(r.available)}</TableCell>
                        <TableCell className="number-cell">{formatBalanceCurrency(r.in_transit)}</TableCell>
                        <TableCell className="number-cell">{formatSignedCurrency(r.inflow)}</TableCell>
                        <TableCell className="number-cell">{formatSignedCurrency(r.outflow)}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="link"
                            size="sm"
                            data-testid="breakdown-drill"
                            onClick={() => {
                              setPlatformFilter(r.platform_code);
                              setPage(1);
                              setBreakdownOpen(false);
                            }}
                          >
                            看明细
                          </Button>
                          <Button
                            variant="link"
                            size="sm"
                            data-testid="breakdown-update"
                            onClick={() => {
                              setMarketValuePlatform(r.platform_code);
                              setBreakdownOpen(false);
                              setMarketValueOpen(true);
                            }}
                          >
                            更新
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="font-medium">
                      <TableCell className="whitespace-nowrap">合计 · 组合口径</TableCell>
                      <TableCell className="number-cell">{formatBalanceCurrency(breakdown.total.available)}</TableCell>
                      <TableCell className="number-cell">{formatBalanceCurrency(breakdown.total.in_transit)}</TableCell>
                      <TableCell className="number-cell">{formatSignedCurrency(breakdown.total.inflow)}</TableCell>
                      <TableCell className="number-cell">{formatSignedCurrency(breakdown.total.outflow)}</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableBody>
                </Table>
              )}
              {(toAmount(breakdown.recon.inflow) !== 0 || toAmount(breakdown.recon.outflow) !== 0) && (
                <p className="text-xs text-muted-foreground">
                  流入/流出各行<b>含</b>平台间转移（对该平台是真进出，与筛该平台时四卡一致）；合计行为<b>组合口径</b>、平台间转移属内部搬运不计，故这两列行和 ≠ 合计：差 = 本期已生效转移（转出{" "}
                  {/* 转出/转入按绝对值展示——「转出 -5,000.00」的负号与词义冗余（#727 N3） */}
                  {formatNumber(Math.abs(toAmount(breakdown.recon.outflow)), 2)} / 转入 {formatNumber(Math.abs(toAmount(breakdown.recon.inflow)), 2)}
                  ，在途腿未生效不计）。可用现金 / 在途资金是时点值，行和 = 合计。
                </p>
              )}
            </>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setBreakdownOpen(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 撤销重估二次确认 */}
      <ConfirmDialog
        open={!!revokeRow}
        onOpenChange={(o) => !o && setRevokeRow(null)}
        title="撤销现金重估"
        description={
          revokeRow
            ? `将删除 ${pname(revokeRow.platform_code)} 在 ${revokeRow.cash_date} 的现金覆盖记录，该日现金回退到自然计算值；若已生成快照需重算才生效。`
            : undefined
        }
        confirmText="确认撤销"
        onConfirm={handleRevoke}
      />
    </div>
  );
}

/** 详情弹窗 kv 行（左标签右值，divide-y） */
function DetailRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <span className="shrink-0 text-muted-foreground">{k}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

/** 构成弹窗移动端卡片（kv 形态；合计卡不传 onDrill/onUpdate 即无操作行） */
function BreakdownCard({
  name,
  available,
  inTransit,
  inflow,
  outflow,
  hit,
  onDrill,
  onUpdate,
}: {
  name: string;
  available: string;
  inTransit: string;
  inflow: string;
  outflow: string;
  hit?: boolean;
  onDrill?: () => void;
  onUpdate?: () => void;
}) {
  return (
    <div className="rounded-lg border p-3" data-testid="breakdown-row">
      <div className="flex items-center justify-between">
        <span className="whitespace-nowrap text-sm font-medium">{name}</span>
        {hit && <span className="text-xs text-muted-foreground">当前筛选</span>}
      </div>
      <div className="mt-2 divide-y text-sm">
        <KvLine k="可用现金" v={formatBalanceCurrency(available)} />
        <KvLine k="在途资金" v={formatBalanceCurrency(inTransit)} />
        <KvLine k="本期流入" v={formatSignedCurrency(inflow)} />
        <KvLine k="本期流出" v={formatSignedCurrency(outflow)} />
      </div>
      {(onDrill || onUpdate) && (
        <div className="mt-2 flex justify-end gap-2">
          {onDrill && (
            <Button variant="link" size="sm" data-testid="breakdown-drill" onClick={onDrill}>
              看明细
            </Button>
          )}
          {onUpdate && (
            <Button variant="link" size="sm" data-testid="breakdown-update" onClick={onUpdate}>
              更新
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function KvLine({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-muted-foreground">{k}</span>
      <span className="number-cell">{v}</span>
    </div>
  );
}
