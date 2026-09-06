"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DatePicker } from "@/components/ui/date-picker";
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
import { Badge } from "@/components/ui/badge";
import { formatCurrency, formatSharesUnit, formatDate, toDateOnly, parseDateOnly, getStatusBadgeVariant, cn } from "@/lib/utils";
import { Plus, ArrowLeft, Loader2, CheckCircle, XCircle, Undo, Filter, Pencil } from "lucide-react";
import Link from "next/link";
import { ShareChangeEventCreate, ApiException } from "@/lib/api";
import { platformApi } from "@/lib/api";
import { EventType } from "@/types/common";
import type { ShareChangeEvent } from "@/types/share-change-event";
import { useUIStore } from "@/stores/uiStore";
import { useQuery } from "@tanstack/react-query";
import {
  useShareChangeEventList,
  useCreateShareChangeEvent,
  useUpdateShareChangeEvent,
  useConfirmShareChangeEvent,
  useUnconfirmShareChangeEvent,
  useCancelShareChangeEvent,
} from "@/hooks/useShareChangeEvent";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import type { DateRange } from "react-day-picker";
import PaginationBar from "@/components/shared/PaginationBar";
import ProductFilterSelect from "@/components/shared/ProductFilterSelect";
import { ProductSelection } from "@/components/shared/ProductFilterDialog";
import EmptyState from "@/components/shared/EmptyState";
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
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import SearchableProductSelect from "@/components/shared/SearchableProductSelect";
import { EVENT_TYPE_LABELS, EventConfirmDialog } from "@/components/shared/event-confirm-dialog";
import { EventEditDialog } from "@/components/shared/event-edit-dialog";
import NameCodeCell from "@/components/shared/NameCodeCell";
import ProductCell from "@/components/shared/ProductCell";
import DatePairCell from "@/components/shared/DatePairCell";

interface ShareChangeEventsContentProps {
  /** 链接前缀：桌面 "/portfolio"，移动 "/m/portfolio" */
  basePath: string;
  variant?: "desktop" | "mobile";
}

const PLATFORM_LEVEL_TYPES: EventType[] = ["cash_dividend", "reinvest_dividend", "forced_adjustment"];

// 状态徽标统一走 Badge variant 语义映射（#127，visual-spec §1.3）
const STATUS_LABELS: Record<string, string> = {
  pending: "待确认",
  confirmed: "已确认",
  cancelled: "已取消",
};

/**
 * 份额变动事件页内容（桌面/移动共用，#276）。
 * 抽离自原 app/portfolio/[code]/share-change-events/page.tsx；
 * 移动端同步继承 #274 的筛选/分页/取消确认能力。
 */
export default function ShareChangeEventsContent({ basePath, variant = "desktop" }: ShareChangeEventsContentProps) {
  const params = useParams();
  const code = params.code as string;
  const addToast = useUIStore((state) => state.addToast);

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [formData, setFormData] = useState<ShareChangeEventCreate>({
    portfolio_code: code,
    event_type: "cash_dividend",
    ex_date: toDateOnly(new Date()),
    entitlement_date: toDateOnly(new Date()),
    platform_code: "",
    product_code: "",
    market: "",
    div_cash: 0,
    reinvest_nav: 0,
    ratio: 0,
    shares_change: 0,
    cash_change: 0,
    notes: "",
  });

  // 筛选状态（#274 服务端筛选）：除息日区间默认不带条件、展示全部事件（#346）
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [eventTypeFilter, setEventTypeFilter] = useState<string | undefined>(undefined);
  const [productFilters, setProductFilters] = useState<ProductSelection[] | undefined>(undefined);
  const [platformFilter, setPlatformFilter] = useState<string | undefined>(undefined);
  const [exRange, setExRange] = useState<DateRange | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filterOpen, setFilterOpen] = useState(false);

  // 查询份额变动事件列表（服务端筛选 + 分页，#274）
  const listParams = {
    portfolio_code: code,
    page,
    page_size: pageSize,
    status: statusFilter,
    event_type: eventTypeFilter,
    products: productFilters?.length
      ? productFilters.map((p) => `${p.code}|${p.market}`).join(",")
      : undefined,
    platform_code: platformFilter,
    ex_date_start: exRange?.from ? toDateOnly(exRange.from) : undefined,
    ex_date_end: exRange?.to ? toDateOnly(exRange.to) : undefined,
  };
  const { data: eventsData, isLoading, isFetching } = useShareChangeEventList(code, listParams);

  // 非默认筛选判定（默认集 = 无任何筛选）：驱动「重置」按钮显隐与空态文案
  const hasNonDefaultFilter =
    statusFilter !== undefined ||
    eventTypeFilter !== undefined ||
    productFilters !== undefined ||
    platformFilter !== undefined ||
    exRange !== undefined;
  const activeFilterCount =
    (statusFilter ? 1 : 0) +
    (eventTypeFilter ? 1 : 0) +
    (productFilters?.length ? 1 : 0) +
    (platformFilter ? 1 : 0) +
    (exRange !== undefined ? 1 : 0);

  const resetFilters = () => {
    setStatusFilter(undefined);
    setEventTypeFilter(undefined);
    setProductFilters(undefined);
    setPlatformFilter(undefined);
    setExRange(undefined);
    setPage(1);
  };

  // 查询平台列表
  const { data: platformsData } = useQuery({
    queryKey: ["platforms"],
    queryFn: () => platformApi.list({ page_size: 100 }),
  });

  const platforms = platformsData?.items || [];

  const events = eventsData?.items || [];
  const total = eventsData?.total ?? 0;

  // #248 确认信息核对弹窗：确认按钮先开弹窗，弹窗内二次点击才发起确认
  const [confirmEventId, setConfirmEventId] = useState<number | null>(null);
  const confirmingEvent = events.find((e) => e.id === confirmEventId) ?? null;
  // 行掉出当前列表时（refetch/他端确认）同步清空 confirmEventId：open 以「行在列表
  // 中」门控属被动关闭（onOpenChange 不触发），不清 state 行重现时弹窗会自发重开
  useEffect(() => {
    if (confirmEventId !== null && !confirmingEvent) {
      setConfirmEventId(null);
    }
  }, [confirmEventId, confirmingEvent]);
  const platformNameMap = useMemo(
    () => new Map((platformsData?.items ?? []).map((plat) => [plat.code, plat.name])),
    [platformsData?.items]
  );

  // 创建/更新/确认/取消确认/取消走统一 hooks
  const createEvent = useCreateShareChangeEvent(code);
  const confirmEvent = useConfirmShareChangeEvent(code);
  const unconfirmEvent = useUnconfirmShareChangeEvent(code);
  const cancelEvent = useCancelShareChangeEvent(code);
  // 编辑入口（#342）：仅 pending 父记录可编辑；顶层持 hook（弹窗关闭时 id=0 不触发）
  const [editingEvent, setEditingEvent] = useState<ShareChangeEvent | null>(null);
  const updateEvent = useUpdateShareChangeEvent(code, editingEvent?.id ?? 0);
  // 取消确认二次确认弹窗（#274；后端保护：SNAPSHOT_DEPENDENCY / CANNOT_UNCONFIRM_CHILD）
  const [unconfirmEventId, setUnconfirmEventId] = useState<number | null>(null);
  // 命中 PLATFORM_NOT_COVERED 时暂存待强制提交的数据，由确认框引导 force_cover 重试
  const [forceCoverData, setForceCoverData] = useState<ShareChangeEventCreate | null>(null);
  const [forceCoverMessage, setForceCoverMessage] = useState("");

  const resetForm = () => {
    setFormData({
      portfolio_code: code,
      event_type: "cash_dividend",
      ex_date: toDateOnly(new Date()),
      entitlement_date: toDateOnly(new Date()),
      platform_code: "",
      product_code: "",
      market: "",
      div_cash: 0,
      reinvest_nav: 0,
      ratio: 0,
      shares_change: 0,
      cash_change: 0,
      notes: "",
    });
  };

  const submitCreate = (data: ShareChangeEventCreate, forceCover = false) => {
    createEvent.mutate(
      { data, forceCover },
      {
        onSuccess: () => {
          setIsDialogOpen(false);
          resetForm();
        },
        onError: (error: unknown) => {
          // 平台覆盖不全：弹确认框引导 force_cover 强制提交
          if (!forceCover && error instanceof ApiException && error.code === "PLATFORM_NOT_COVERED") {
            setForceCoverData(data);
            setForceCoverMessage(error.message);
          }
        },
      }
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.product_code) {
      addToast({
        type: "error",
        title: "表单校验失败",
        message: "请填写产品代码",
      });
      return;
    }

    if (PLATFORM_LEVEL_TYPES.includes(formData.event_type) && !formData.platform_code) {
      // 与申赎 R1 同口径：自定义平台控件无原生 required，须手动拦截
      addToast({
        type: "error",
        title: "表单校验失败",
        message: "请选择平台",
      });
      return;
    }

    // #343 双保险：基金级事件不渲染平台选择器，空串归一为 undefined 再提交
    //（后端 service 同口径归一，此处仅避免无效载荷）
    submitCreate({ ...formData, platform_code: formData.platform_code || undefined });
  };

  // 筛选栏控件（visual-spec §9）：顺序 = 除息日区间 → 状态 → 事件类型 → 产品 → 平台；
  // 控件统一 h-9（移动端全宽），「全部 X」用 "all" 哨兵
  const rangeWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[240px]";
  const selectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[150px]";
  const productSelectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[220px]";
  const filterControls = (
    <>
      <DateRangePicker
        value={exRange}
        onChange={(r) => {
          setExRange(r);
          setPage(1);
        }}
        placeholder="全部时间"
        numberOfMonths={variant === "mobile" ? 1 : 2}
        className={rangeWidth}
      />
      <Select
        value={statusFilter ?? "all"}
        onValueChange={(v) => {
          setStatusFilter(v === "all" ? undefined : v);
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部状态" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部状态</SelectItem>
          <SelectItem value="pending">待确认</SelectItem>
          <SelectItem value="confirmed">已确认</SelectItem>
          <SelectItem value="cancelled">已取消</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={eventTypeFilter ?? "all"}
        onValueChange={(v) => {
          setEventTypeFilter(v === "all" ? undefined : v);
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部类型</SelectItem>
          {Object.entries(EVENT_TYPE_LABELS).map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <ProductFilterSelect
        variant={variant}
        value={productFilters ?? []}
        onChange={(selection) => {
          setProductFilters(selection.length ? selection : undefined);
          setPage(1);
        }}
        className={productSelectWidth}
      />
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
      {hasNonDefaultFilter && (
        <Button variant="ghost" size="sm" className="h-9" onClick={resetFilters}>
          重置
        </Button>
      )}
    </>
  );

  // 表单双列布局窄屏降为单列（移动端控件全宽）
  const formGrid = variant === "mobile" ? "grid grid-cols-1 gap-4" : "grid grid-cols-2 gap-4";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href={`${basePath}/${code}`}>
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div>
            <h1 className={variant === "mobile" ? "text-2xl font-bold" : "text-3xl font-bold tracking-tight"}>
              份额变动事件
            </h1>
            <p className="text-muted-foreground">组合代码: {code}</p>
          </div>
        </div>
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen} modal={false}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              新建事件
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>新建份额变动事件</DialogTitle>
              <DialogDescription>
                记录基金分红、拆分、合并等份额变动事件
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit}>
              <div className="space-y-4 py-4">
                <div className={formGrid}>
                  <div className="space-y-2">
                    <Label htmlFor="event_type">事件类型</Label>
                    <Select
                      value={formData.event_type}
                      onValueChange={(value) => setFormData({ ...formData, event_type: value as EventType })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(EVENT_TYPE_LABELS).map(([key, label]) => (
                          <SelectItem key={key} value={key}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="product_code">产品代码</Label>
                    <SearchableProductSelect
                      id="product_code"
                      value={
                        formData.product_code
                          ? { code: formData.product_code, market: formData.market ?? "" }
                          : null
                      }
                      onChange={(v) =>
                        setFormData({
                          ...formData,
                          product_code: v?.code ?? "",
                          market: v?.market ?? "",
                        })
                      }
                    />
                  </div>
                </div>

                {/* 平台选择器：仅平台级事件显示 */}
                {PLATFORM_LEVEL_TYPES.includes(formData.event_type) && (
                  <div className="space-y-2">
                    <Label htmlFor="platform_code">平台</Label>
                    <SearchablePlatformSelect
                      platforms={platforms}
                      value={formData.platform_code || null}
                      onChange={(v) => setFormData({ ...formData, platform_code: v ?? "" })}
                      placeholder="选择平台"
                      id="platform_code"
                    />
                  </div>
                )}

                {/* 日期字段顺序 = 业务时序（#355）：权益登记日先于除息日（后端强制 ex_date > entitlement_date），
                    与确认弹窗既有顺序一致 */}
                <div className={formGrid}>
                  <div className="space-y-2">
                    <Label htmlFor="entitlement_date">权益登记日</Label>
                    <DatePicker
                      date={parseDateOnly(formData.entitlement_date)}
                      onSelect={(date) => {
                        setFormData({ ...formData, entitlement_date: toDateOnly(date) })
                      }}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="ex_date">除息日</Label>
                    <DatePicker
                      date={parseDateOnly(formData.ex_date)}
                      onSelect={(date) => {
                        setFormData({ ...formData, ex_date: toDateOnly(date) })
                      }}
                    />
                  </div>
                </div>

                {/* 根据事件类型显示不同字段 */}
                {(formData.event_type === "cash_dividend" || formData.event_type === "reinvest_dividend") && (
                  <>
                    <div className={formGrid}>
                      <div className="space-y-2">
                        <Label htmlFor="div_cash">每份分红金额（元）</Label>
                        <Input
                          id="div_cash"
                          type="number"
                          step="0.0001"
                          value={formData.div_cash}
                          onChange={(e) => setFormData({ ...formData, div_cash: parseFloat(e.target.value) || 0 })}
                        />
                      </div>
                      {formData.event_type === "reinvest_dividend" && (
                        <div className="space-y-2">
                          <Label htmlFor="reinvest_nav">再投资净值</Label>
                          <Input
                            id="reinvest_nav"
                            type="number"
                            step="0.0001"
                            value={formData.reinvest_nav}
                            onChange={(e) => setFormData({ ...formData, reinvest_nav: parseFloat(e.target.value) || 0 })}
                          />
                        </div>
                      )}
                    </div>
                  </>
                )}

                {(formData.event_type === "share_split" || formData.event_type === "share_merge" || formData.event_type === "bonus_share") && (
                  <div className="space-y-2">
                    <Label htmlFor="ratio">比例</Label>
                    <Input
                      id="ratio"
                      type="number"
                      step="0.0001"
                      value={formData.ratio}
                      onChange={(e) => setFormData({ ...formData, ratio: parseFloat(e.target.value) || 0 })}
                      placeholder="如：拆分比例 2.0 表示1份变2份"
                    />
                  </div>
                )}

                {formData.event_type === "forced_adjustment" && (
                  <div className={formGrid}>
                    <div className="space-y-2">
                      <Label htmlFor="shares_change">份额变化</Label>
                      <Input
                        id="shares_change"
                        type="number"
                        step="0.01"
                        value={formData.shares_change}
                        onChange={(e) => setFormData({ ...formData, shares_change: parseFloat(e.target.value) || 0 })}
                        placeholder="正数增加，负数减少（份额 2 位小数）"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="cash_change">现金变化</Label>
                      <Input
                        id="cash_change"
                        type="number"
                        step="0.01"
                        value={formData.cash_change}
                        onChange={(e) => setFormData({ ...formData, cash_change: parseFloat(e.target.value) || 0 })}
                        placeholder="正数增加，负数减少"
                      />
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="notes">备注</Label>
                  <Input
                    id="notes"
                    value={formData.notes}
                    onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                    placeholder="可选"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setIsDialogOpen(false)}>
                  取消
                </Button>
                <Button type="submit" disabled={createEvent.isPending}>
                  {createEvent.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  创建
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>事件列表</CardTitle>
          <CardDescription>查看和管理份额变动事件</CardDescription>
        </CardHeader>
        <CardContent>
          {/* 筛选栏（#274，规范 §9：表格卡片内顶部）；移动端为折叠面板 + 激活计数 Badge */}
          {variant === "mobile" ? (
            <div className="mb-3 space-y-2">
              <Button variant="outline" size="sm" className="h-9" onClick={() => setFilterOpen((v) => !v)}>
                <Filter className="mr-2 h-4 w-4" />
                筛选
                {activeFilterCount > 0 && (
                  <Badge variant="default" className="ml-2">
                    {activeFilterCount}
                  </Badge>
                )}
              </Button>
              {filterOpen && <div className="grid grid-cols-1 gap-2">{filterControls}</div>}
            </div>
          ) : (
            <div className="mb-3 flex flex-wrap items-center gap-2">{filterControls}</div>
          )}
          {isLoading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              加载中...
            </div>
          ) : (
            <>
              {/* 规范 §14：筛选/翻页局部刷新保留旧数据，表格半透明 + 右上角小 spinner */}
              <div className="relative">
                {isFetching && (
                  <Loader2 className="absolute right-2 top-2 z-10 h-4 w-4 animate-spin text-muted-foreground" />
                )}
                <Table className={cn(isFetching && "opacity-50")}>
                  <TableHeader>
                    <TableRow>
                      <TableHead>事件类型</TableHead>
                      <TableHead>产品</TableHead>
                      <TableHead>平台</TableHead>
                      <TableHead>权益登记/除息日</TableHead>
                      <TableHead className="number-cell">份额变化</TableHead>
                      <TableHead className="number-cell">现金变化</TableHead>
                      <TableHead>状态</TableHead>
                      <TableHead>操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {events.map((event) => (
                      <TableRow key={event.id}>
                        <TableCell>{EVENT_TYPE_LABELS[event.event_type] || event.event_type}</TableCell>
                        <TableCell>
                          {event.product_code ? (
                            <ProductCell
                              code={event.product_code}
                              name={event.product_name}
                              market={event.market}
                            />
                          ) : (
                            "--"
                          )}
                        </TableCell>
                        <TableCell>
                          {event.platform_code ? (
                            <NameCodeCell code={event.platform_code} nameMap={platformNameMap} />
                          ) : (
                            "全部"
                          )}
                        </TableCell>
                        <TableCell>
                          {/* 成对日期合并单列（#355）：上行权益登记日、下行除息日，与业务时序一致 */}
                          <DatePairCell
                            topLabel="权益登记日"
                            topValue={formatDate(event.entitlement_date)}
                            bottomLabel="除息日"
                            bottomValue={formatDate(event.ex_date)}
                          />
                        </TableCell>
                        <TableCell className="number-cell">
                          {formatSharesUnit(event.shares_change)}
                        </TableCell>
                        <TableCell className="number-cell">
                          {formatCurrency(event.cash_change)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={getStatusBadgeVariant(event.status)}>
                            {STATUS_LABELS[event.status] || event.status}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-2">
                            {event.status === "pending" && (
                              <>
                                {/* 编辑（#342）：仅 pending 父记录；子记录恒为 confirmed，parent 守卫为防御性 */}
                                {!event.parent_event_id && (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    title="编辑"
                                    onClick={() => setEditingEvent(event)}
                                  >
                                    <Pencil className="h-4 w-4" />
                                  </Button>
                                )}
                                <Button
                                  size="sm"
                                  variant="outline"
                                  title="确认"
                                  onClick={() => setConfirmEventId(event.id)}
                                >
                                  <CheckCircle className="h-4 w-4 text-success" />
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  title="取消"
                                  onClick={() => cancelEvent.mutate(event.id)}
                                  disabled={cancelEvent.isPending}
                                >
                                  <XCircle className="h-4 w-4 text-destructive" />
                                </Button>
                              </>
                            )}
                            {/* 取消确认（#274）：子记录（基金级确认时拆出）不展示入口 */}
                            {event.status === "confirmed" && !event.parent_event_id && (
                              <Button
                                size="sm"
                                variant="outline"
                                title="取消确认"
                                onClick={() => setUnconfirmEventId(event.id)}
                              >
                                <Undo className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {/* 空态：默认筛选集下为空 = 暂无记录；非默认筛选下为空 = 引导重置（规范 §8 变体②） */}
              {events.length === 0 &&
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
                  <EmptyState message="暂无份额变动事件" />
                ))}
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
            </>
          )}
        </CardContent>
      </Card>

      {/* #248：确认信息核对弹窗（事件字段均落库，无预览请求），弹窗内二次确认才发起请求 */}
      <EventConfirmDialog
        open={confirmingEvent !== null}
        onOpenChange={(open) => !open && setConfirmEventId(null)}
        event={confirmingEvent}
        platformNameMap={platformNameMap}
        isConfirming={confirmEvent.isPending}
        onConfirm={() => {
          if (confirmEventId === null) return;
          confirmEvent.mutate(confirmEventId, { onSuccess: () => setConfirmEventId(null) });
        }}
      />

      {/* #342：pending 事件编辑弹窗（PUT 直改，字段以 ShareChangeEventUpdate 为准） */}
      <EventEditDialog
        open={editingEvent !== null}
        onOpenChange={(open) => !open && setEditingEvent(null)}
        event={editingEvent}
        platformNameMap={platformNameMap}
        isSaving={updateEvent.isPending}
        onSubmit={(payload) =>
          updateEvent.mutate(payload, { onSuccess: () => setEditingEvent(null) })
        }
      />

      {/* PLATFORM_NOT_COVERED 强制提交确认 */}
      <AlertDialog open={!!forceCoverData} onOpenChange={(open) => !open && setForceCoverData(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>平台覆盖不完整</AlertDialogTitle>
            <AlertDialogDescription>
              {forceCoverMessage || "平台级事件未覆盖全部有持仓的平台。"}
              确定要忽略此检查强制提交吗？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (forceCoverData) {
                  submitCreate(forceCoverData, true);
                }
                setForceCoverData(null);
              }}
            >
              强制提交
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 取消确认二次确认（#274，语义对齐调仓页；后端保护经 getErrorMessage 透传） */}
      <AlertDialog
        open={unconfirmEventId !== null}
        onOpenChange={(open) => !open && setUnconfirmEventId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>取消确认</AlertDialogTitle>
            <AlertDialogDescription>
              取消确认后事件回退为待确认状态，可重新修改或删除。是否继续？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (unconfirmEventId !== null) {
                  unconfirmEvent.mutate(unconfirmEventId, {
                    onSuccess: () => setUnconfirmEventId(null),
                  });
                }
              }}
            >
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
