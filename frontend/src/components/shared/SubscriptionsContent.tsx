"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { DateRangePicker } from "@/components/ui/date-range-picker";
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
import { formatCurrency, formatSharesUnit, formatNav, formatDate, toDateOnly, parseDateOnly, getStatusBadgeVariant, cn } from "@/lib/utils";
import { validatePlatformCode, parsePositiveNumber } from "@/lib/validation";
import { Badge } from "@/components/ui/badge";
import { TRADE_DIRECTION_COLORS } from "@/lib/colors";
import { Plus, ArrowLeft, CheckCircle, XCircle, Loader2, Pencil, Trash2, Undo, Filter } from "lucide-react";
import Link from "next/link";
import type { DateRange } from "react-day-picker";
import { isSameDay, subYears } from "date-fns";
import type { SubscriptionListParams } from "@/lib/api";
import type { Subscription, SubscriptionUpdate } from "@/types/subscription";
import {
  useSubscriptionList,
  useCreateSubscription,
  useUpdateSubscription,
  useConfirmSubscription,
  useCancelSubscription,
  useUnconfirmSubscription,
  useDeleteSubscription,
} from "@/hooks/useTrade";
import { useInvestorList } from "@/hooks/useInvestor";
import { usePlatformList } from "@/hooks/usePlatform";
import { usePortfolio } from "@/hooks/usePortfolio";
import { useInvestorAvailableShares } from "@/hooks/usePosition";
import { useUIStore } from "@/stores/uiStore";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import PaginationBar from "@/components/shared/PaginationBar";
import NameCodeCell from "@/components/shared/NameCodeCell";
import DatePairCell from "@/components/shared/DatePairCell";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { SubscriptionConfirmDialog } from "@/components/shared/SubscriptionConfirmDialog";

interface SubscriptionsContentProps {
  /** 链接前缀：桌面 "/portfolio"，移动 "/m/portfolio" */
  basePath: string;
  variant?: "desktop" | "mobile";
}

type ConfirmState =
  | { action: "confirm"; id: number }
  | { action: "cancel"; id: number }
  | { action: "unconfirm"; id: number }
  | { action: "delete"; id: number }
  | null;

const CONFIRM_TEXT: Record<"confirm" | "cancel" | "unconfirm" | "delete", { title: string; desc: string }> = {
  confirm: { title: "确认申请", desc: "确定要确认该申请吗？" },
  cancel: { title: "取消申请", desc: "确定要取消该申请吗？" },
  unconfirm: { title: "取消确认", desc: "取消后可以修改或删除。是否继续？" },
  delete: { title: "删除申请", desc: "删除后将影响后续快照数据，建议先取消确认再删除。是否继续？" },
};

/** 默认申购日期区间 = 快捷项「近1年」（#125 决策⑤，区间语义与 DateRangePicker 快捷项一致） */
function defaultApplyRange(): DateRange {
  return { from: subYears(new Date(), 1), to: new Date() };
}

/** 与默认区间一致（isSameDay 双端比较）→ 视为「无筛选」默认态，用于重置按钮显隐与空态文案 */
function isDefaultApplyRange(range: DateRange | undefined): boolean {
  if (!range?.from || !range.to) return false;
  const d = defaultApplyRange();
  return !!d.from && !!d.to && isSameDay(range.from, d.from) && isSameDay(range.to, d.to);
}

/**
 * 申购赎回页内容（桌面/移动共用）。
 * 抽离自原 app/portfolio/[code]/subscriptions/page.tsx，
 * 用 AlertDialog 替换原生 confirm/alert，删除成功改用 toast 提示。
 */
export default function SubscriptionsContent({ basePath, variant = "desktop" }: SubscriptionsContentProps) {
  const params = useParams();
  const router = useRouter();
  const code = params.code as string;

  // 筛选状态（#125 服务端筛选）：applyRange 默认最近 1 年（决策⑤，惰性初始化避免每渲染重算）
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [subTypeFilter, setSubTypeFilter] = useState<string | undefined>(undefined);
  const [investorFilter, setInvestorFilter] = useState<string | undefined>(undefined);
  const [platformFilter, setPlatformFilter] = useState<string | undefined>(undefined);
  const [applyRange, setApplyRange] = useState<DateRange | undefined>(() => defaultApplyRange());
  const [confirmRange, setConfirmRange] = useState<DateRange | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filterOpen, setFilterOpen] = useState(false);

  // 空筛选字段为 undefined，axios 不传参；日期区间映射 start/end 闭区间参数
  const listParams: SubscriptionListParams = {
    portfolio_code: code,
    page,
    page_size: pageSize,
    status: statusFilter,
    sub_type: subTypeFilter,
    investor_code: investorFilter,
    platform_code: platformFilter,
    apply_date_start: applyRange?.from ? toDateOnly(applyRange.from) : undefined,
    apply_date_end: applyRange?.to ? toDateOnly(applyRange.to) : undefined,
    confirm_date_start: confirmRange?.from ? toDateOnly(confirmRange.from) : undefined,
    confirm_date_end: confirmRange?.to ? toDateOnly(confirmRange.to) : undefined,
  };
  const { data, isLoading, isFetching } = useSubscriptionList(listParams);
  const createSubscription = useCreateSubscription();
  const confirmSubscription = useConfirmSubscription();
  const cancelSubscription = useCancelSubscription();
  const unconfirmSubscription = useUnconfirmSubscription();
  const { data: investorsData } = useInvestorList({ page_size: 100 });
  const { data: platformsData } = usePlatformList({ page_size: 100 });
  const { data: portfolio } = usePortfolio(code);

  const subscriptions = data?.items || [];
  const total = data?.total ?? 0;
  const investors = investorsData?.items || [];
  const platforms = platformsData?.items || [];
  const isDraft = portfolio?.status === "draft";

  // 非默认筛选判定（默认集 = 仅 applyRange 为最近 1 年）：驱动「重置」按钮显隐与空态文案
  const hasNonDefaultFilter =
    statusFilter !== undefined ||
    subTypeFilter !== undefined ||
    investorFilter !== undefined ||
    platformFilter !== undefined ||
    confirmRange !== undefined ||
    applyRange === undefined ||
    !isDefaultApplyRange(applyRange);
  const activeFilterCount =
    (statusFilter ? 1 : 0) +
    (subTypeFilter ? 1 : 0) +
    (investorFilter ? 1 : 0) +
    (platformFilter ? 1 : 0) +
    (confirmRange ? 1 : 0) +
    (applyRange === undefined || !isDefaultApplyRange(applyRange) ? 1 : 0);

  const resetFilters = () => {
    setStatusFilter(undefined);
    setSubTypeFilter(undefined);
    setInvestorFilter(undefined);
    setPlatformFilter(undefined);
    setApplyRange(defaultApplyRange());
    setConfirmRange(undefined);
    setPage(1);
  };

  // 表格 name 显示映射（issue #124）：复用已加载列表，零新增请求；依赖 react-query 稳定引用避免每渲染重建
  const investorNameMap = useMemo(
    () => new Map((investorsData?.items ?? []).map((inv) => [inv.code, inv.name])),
    [investorsData?.items]
  );
  const platformNameMap = useMemo(
    () => new Map((platformsData?.items ?? []).map((plat) => [plat.code, plat.name])),
    [platformsData?.items]
  );

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [subType, setSubType] = useState<"subscribe" | "redeem">("subscribe");
  const [formData, setFormData] = useState({
    investor_code: "",
    platform_code: "",
    amount: "",
    shares: "",
    apply_date: toDateOnly(new Date()),
  });
  const [confirmState, setConfirmState] = useState<ConfirmState>(null);
  // #248 确认弹窗门控：记录须仍在当前列表中（列表 refetch/翻页导致行消失时弹窗随之关闭，
  // 防止空内容弹窗仍可触发确认）
  const confirmingSub =
    confirmState?.action === "confirm"
      ? subscriptions.find((s) => s.id === confirmState.id) ?? null
      : null;
  // 行掉出当前列表时（refetch/翻页/他端确认）同步清空 confirmState：open 以「行在
  // 列表中」门控属被动关闭（onOpenChange 不触发），不清 state 行重现时弹窗会自发重开
  useEffect(() => {
    if (confirmState?.action === "confirm" && !confirmingSub) {
      setConfirmState(null);
    }
  }, [confirmState, confirmingSub]);
  const [editHint, setEditHint] = useState(false);
  // pending 申赎编辑（issue #202）：editingSub 非空即打开编辑 Dialog
  const [editingSub, setEditingSub] = useState<Subscription | null>(null);
  const [editFormData, setEditFormData] = useState({
    amount: "",
    shares: "",
    apply_date: toDateOnly(new Date()),
    notes: "",
  });
  // 顶层无条件调用（hooks 规则）；id=0 时 mutate 不会被触发（Dialog 打开时 editingSub 必有 id）
  const updateSubscription = useUpdateSubscription(editingSub?.id ?? 0);

  // 投资人可用份额（赎回口径，issue #67）：仅赎回模式且已选投资人时查询
  const { data: availableData, isFetching: availableFetching } = useInvestorAvailableShares(
    code,
    formData.investor_code,
    subType === "redeem"
  );
  const availableShares = availableData?.available_shares;

  // 全部赎回：直接填入后端返回的精确可用份额，不做任何舍入/格式化
  const handleRedeemAll = () => {
    if (availableShares === undefined || availableShares <= 0) return;
    setFormData({ ...formData, shares: String(availableShares) });
  };

  // 删除走统一 hook（原内联 mutation 的 invalidate key 与 hooks 实际 key 结构不匹配，列表不刷新）
  const deleteSubscriptionMutation = useDeleteSubscription();
  const addToast = useUIStore((state) => state.addToast);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const platformError = validatePlatformCode(formData.platform_code);
    if (platformError) {
      addToast({ type: "error", title: "表单校验失败", message: platformError });
      return;
    }
    const payload = {
      portfolio_code: code,
      investor_code: formData.investor_code,
      platform_code: formData.platform_code,
      sub_type: subType,
      apply_date: formData.apply_date,
      ...(subType === "subscribe" ? { amount: parseFloat(formData.amount) } : { shares: parseFloat(formData.shares) }),
    };
    createSubscription.mutate(payload, {
      onSuccess: () => {
        setIsDialogOpen(false);
        setFormData({ investor_code: "", platform_code: "", amount: "", shares: "", apply_date: toDateOnly(new Date()) });
        if (isDraft) router.push(`${basePath}/${code}`);
      },
    });
  };

  // confirm 动作由 SubscriptionConfirmDialog 内部触发（#248），此处仅处理其余三个动作
  const runConfirm = () => {
    if (!confirmState) return;
    const { action, id } = confirmState;
    if (action === "cancel") cancelSubscription.mutate(id);
    else if (action === "unconfirm") unconfirmSubscription.mutate(id);
    else if (action === "delete") deleteSubscriptionMutation.mutate(id);
    setConfirmState(null);
  };

  // 打开编辑 Dialog 并按类型预填（issue #202）：申购预填金额、赎回预填份额
  const openEditDialog = (sub: Subscription) => {
    setEditingSub(sub);
    setEditFormData({
      amount: sub.sub_type === "subscribe" ? String(sub.amount ?? "") : "",
      shares: sub.sub_type === "redeem" ? String(sub.shares ?? "") : "",
      apply_date: sub.apply_date,
      notes: sub.notes ?? "",
    });
  };

  const handleEditSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSub) return;
    // 显式传值（非 truthy 判断，防清空字段被静默丢弃，PR #204 评审）：
    // 类型字段与 apply_date 恒传（均有预填值）；notes 空串传 null 清除备注
    //（后端仅 notes 放行 null，其余字段 null 拒 INVALID_PARAM）
    const payload: SubscriptionUpdate = {
      apply_date: editFormData.apply_date,
      notes: editFormData.notes || null,
    };
    if (editingSub.sub_type === "subscribe") {
      const amount = parsePositiveNumber(editFormData.amount);
      if (amount === null) return; // required/min 已拦，双保险
      payload.amount = amount;
    } else {
      const shares = parsePositiveNumber(editFormData.shares);
      if (shares === null) return;
      payload.shares = shares;
    }
    // 失败时 hook 已 toast，Dialog 保持打开可重试（不挂 onError 关闭逻辑）
    updateSubscription.mutate(payload, { onSuccess: () => setEditingSub(null) });
  };

  // 筛选栏控件（visual-spec §9）：顺序 = 申购日期区间 → 确认日期区间 → 状态 → 投资人 → 平台 → 类型；
  // 控件统一 h-9，下拉走 ui/select（「全部 X」用 "all" 哨兵，Radix SelectItem 不允许空串值）；
  // 平台为 SearchablePlatformSelect（Popover，null 哨兵）
  const rangeWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[240px]";
  const selectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[150px]";
  const filterControls = (
    <>
      <DateRangePicker
        value={applyRange}
        onChange={(r) => {
          setApplyRange(r);
          setPage(1);
        }}
        placeholder="申购日期"
        numberOfMonths={variant === "mobile" ? 1 : 2}
        className={rangeWidth}
      />
      <DateRangePicker
        value={confirmRange}
        onChange={(r) => {
          setConfirmRange(r);
          setPage(1);
        }}
        placeholder="确认日期"
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
        value={investorFilter ?? "all"}
        onValueChange={(v) => {
          setInvestorFilter(v === "all" ? undefined : v);
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部投资人" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部投资人</SelectItem>
          {investors.map((inv) => (
            <SelectItem key={inv.code} value={inv.code}>
              {inv.name} ({inv.code})
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
        value={subTypeFilter ?? "all"}
        onValueChange={(v) => {
          setSubTypeFilter(v === "all" ? undefined : v);
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部类型</SelectItem>
          <SelectItem value="subscribe">申购</SelectItem>
          <SelectItem value="redeem">赎回</SelectItem>
        </SelectContent>
      </Select>
      {hasNonDefaultFilter && (
        <Button variant="ghost" size="sm" className="h-9" onClick={resetFilters}>
          重置
        </Button>
      )}
    </>
  );

  if (isLoading) return <LoadingState />;

  return (
    <div className="space-y-6">
      {isDraft && (
        <Alert>
          <AlertDescription>首次申购将激活组合，初始净值固定为 1.0000</AlertDescription>
        </Alert>
      )}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href={`${basePath}/${code}`}>
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div>
            <h1 className={variant === "mobile" ? "text-2xl font-bold" : "text-3xl font-bold tracking-tight"}>
              申购赎回
            </h1>
            <p className="text-muted-foreground">组合代码: {code}</p>
          </div>
        </div>
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen} modal={false}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              {isDraft ? "首次申购激活" : "提交申请"}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{isDraft ? "首次申购激活" : "提交申请"}</DialogTitle>
              <DialogDescription>
                {isDraft ? "提交首次申购以激活组合，初始净值固定为 1.0000" : "提交申购或赎回申请"}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit}>
              <div className="space-y-4 py-4">
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant={subType === "subscribe" ? "default" : "outline"}
                    onClick={() => setSubType("subscribe")}
                    className="flex-1"
                  >
                    申购
                  </Button>
                  <Button
                    type="button"
                    variant={subType === "redeem" ? "default" : "outline"}
                    onClick={() => setSubType("redeem")}
                    className="flex-1"
                  >
                    赎回
                  </Button>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="investor_code">投资人</Label>
                  <select
                    id="investor_code"
                    value={formData.investor_code}
                    onChange={(e) => setFormData({ ...formData, investor_code: e.target.value })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    required
                  >
                    <option value="">请选择投资人</option>
                    {investors.map((inv) => (
                      <option key={inv.code} value={inv.code}>
                        {inv.name} ({inv.code})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="platform_code">交易平台</Label>
                  <SearchablePlatformSelect
                    platforms={platforms}
                    value={formData.platform_code || null}
                    onChange={(v) => setFormData({ ...formData, platform_code: v ?? "" })}
                    placeholder="请选择平台"
                    id="platform_code"
                  />
                </div>
                {subType === "subscribe" ? (
                  <div className="space-y-2">
                    <Label htmlFor="amount">金额（元）</Label>
                    <Input
                      id="amount"
                      type="number"
                      step="0.01"
                      min="0.01"
                      value={formData.amount}
                      onChange={(e) => setFormData({ ...formData, amount: e.target.value })}
                      required
                    />
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label htmlFor="shares">份额</Label>
                      {formData.investor_code && availableShares !== undefined && (
                        <span className="text-xs text-muted-foreground">
                          可用 {formatSharesUnit(availableShares)}
                        </span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <Input
                        id="shares"
                        type="number"
                        step="0.01"
                        min="0.01"
                        value={formData.shares}
                        onChange={(e) => setFormData({ ...formData, shares: e.target.value })}
                        required
                        className="flex-1"
                      />
                      <Button
                        type="button"
                        variant="outline"
                        onClick={handleRedeemAll}
                        disabled={
                          !formData.investor_code ||
                          availableFetching ||
                          availableShares === undefined ||
                          availableShares <= 0
                        }
                      >
                        {availableFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : "全部赎回"}
                      </Button>
                    </div>
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="apply_date">申请日期</Label>
                  <DatePicker
                    date={parseDateOnly(formData.apply_date)}
                    onSelect={(date) => {
                      setFormData({ ...formData, apply_date: toDateOnly(date) });
                    }}
                    showTradingDays
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={createSubscription.isPending}>
                  {createSubscription.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  提交申请
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>申请记录</CardTitle>
          <CardDescription>申购和赎回记录</CardDescription>
        </CardHeader>
        <CardContent>
          {/* 筛选栏（规范 §9：表格卡片内顶部）；移动端为折叠面板 + 激活计数 Badge */}
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
          {/* 规范 §14：筛选/翻页局部刷新保留旧数据，表格半透明 + 右上角小 spinner */}
          <div className="relative">
            {isFetching && (
              <Loader2 className="absolute right-2 top-2 z-10 h-4 w-4 animate-spin text-muted-foreground" />
            )}
            <Table className={cn(isFetching && "opacity-50")}>
              <TableHeader>
                <TableRow>
                  <TableHead>投资人</TableHead>
                  <TableHead>平台</TableHead>
                  <TableHead>类型</TableHead>
                  {/* 金额/份额拆独立列（#247，对齐调仓列表 #173 列语义），勿合并回单列 */}
                  <TableHead className="number-cell">金额</TableHead>
                  <TableHead className="number-cell">份额</TableHead>
                  <TableHead className="number-cell">净值</TableHead>
                  <TableHead>申请/确认日期</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {subscriptions.map((sub) => (
                  <TableRow key={sub.id}>
                    <TableCell>
                      <NameCodeCell code={sub.investor_code} nameMap={investorNameMap} />
                    </TableCell>
                    <TableCell>
                      {sub.platform_code ? <NameCodeCell code={sub.platform_code} nameMap={platformNameMap} /> : "--"}
                    </TableCell>
                    <TableCell>
                      {/* 方向标识无状态语义：neutral badge + 方向色圆点（lib/colors，#127） */}
                      <Badge variant="neutral">
                        <span
                          className="mr-1.5 h-1.5 w-1.5 rounded-full"
                          style={{ background: TRADE_DIRECTION_COLORS[sub.sub_type === "subscribe" ? "buy" : "sell"] }}
                        />
                        {sub.sub_type === "subscribe" ? "申购" : "赎回"}
                      </Badge>
                    </TableCell>
                    <TableCell className="number-cell">{formatCurrency(sub.amount)}</TableCell>
                    <TableCell className="number-cell">{formatSharesUnit(sub.shares)}</TableCell>
                    <TableCell className="number-cell">{formatNav(sub.unit_price)}</TableCell>
                    <TableCell>
                      {/* 成对日期合并单列（#355）：上行申请日、下行确认日；pending 的确认日是预计值，下行内联标注 */}
                      <DatePairCell
                        topLabel="申请日期"
                        topValue={formatDate(sub.apply_date)}
                        bottomLabel="确认日期"
                        bottomValue={sub.confirm_date ? formatDate(sub.confirm_date) : "--"}
                        estimated={sub.status === "pending" && !!sub.confirm_date}
                      />
                    </TableCell>
                    <TableCell>
                      <Badge variant={getStatusBadgeVariant(sub.status)}>
                        {sub.status === "confirmed" ? "已确认" : sub.status === "pending" ? "待确认" : "已取消"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {/* 操作按钮对齐后端允许矩阵（issue #202）：pending=编辑/确认/取消/删除；
                          confirmed=取消确认/修改引导，无删除（后端 CANNOT_DELETE_CONFIRMED） */}
                      {sub.status === "pending" && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openEditDialog(sub)}
                            title="编辑"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmState({ action: "confirm", id: sub.id })}
                            disabled={confirmSubscription.isPending}
                            title="确认"
                          >
                            <CheckCircle className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmState({ action: "cancel", id: sub.id })}
                            disabled={cancelSubscription.isPending}
                            title="取消"
                          >
                            <XCircle className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmState({ action: "delete", id: sub.id })}
                            disabled={deleteSubscriptionMutation.isPending}
                            title="删除"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                      {sub.status === "confirmed" && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmState({ action: "unconfirm", id: sub.id })}
                            title="取消确认"
                          >
                            <Undo className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setEditHint(true)} title="修改">
                            <Pencil className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {/* 空态：默认筛选集下为空 = 暂无记录；非默认筛选下为空 = 引导重置（规范 §8 变体②） */}
          {subscriptions.length === 0 &&
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
              <EmptyState message="暂无申请记录" />
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
        </CardContent>
      </Card>

      {/* pending 申赎编辑 Dialog（issue #202）：投资人/平台/类型后端不支持改，只读展示 */}
      <Dialog open={!!editingSub} onOpenChange={(open) => !open && setEditingSub(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑申请</DialogTitle>
            <DialogDescription>仅待确认申请可编辑，投资人/平台/类型不可修改</DialogDescription>
          </DialogHeader>
          {editingSub && (
            <form onSubmit={handleEditSubmit}>
              <div className="space-y-4 py-4">
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">投资人</span>
                    <span>
                      {investorNameMap.get(editingSub.investor_code) ?? editingSub.investor_code}（{editingSub.investor_code}）
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">平台</span>
                    <span>
                      {platformNameMap.get(editingSub.platform_code) ?? editingSub.platform_code}（{editingSub.platform_code}）
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">类型</span>
                    <Badge variant="neutral">
                      <span
                        className="mr-1.5 h-1.5 w-1.5 rounded-full"
                        style={{ background: TRADE_DIRECTION_COLORS[editingSub.sub_type === "subscribe" ? "buy" : "sell"] }}
                      />
                      {editingSub.sub_type === "subscribe" ? "申购" : "赎回"}
                    </Badge>
                  </div>
                </div>
                {editingSub.sub_type === "subscribe" ? (
                  <div className="space-y-2">
                    <Label htmlFor="edit_amount">金额（元）</Label>
                    <Input
                      id="edit_amount"
                      type="number"
                      step="0.01"
                      min="0.01"
                      value={editFormData.amount}
                      onChange={(e) => setEditFormData({ ...editFormData, amount: e.target.value })}
                      required
                    />
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label htmlFor="edit_shares">份额</Label>
                    <Input
                      id="edit_shares"
                      type="number"
                      step="0.01"
                      min="0.01"
                      value={editFormData.shares}
                      onChange={(e) => setEditFormData({ ...editFormData, shares: e.target.value })}
                      required
                    />
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="edit_apply_date">申请日期</Label>
                  <DatePicker
                    date={parseDateOnly(editFormData.apply_date)}
                    onSelect={(date) => {
                      setEditFormData({ ...editFormData, apply_date: toDateOnly(date) });
                    }}
                    showTradingDays
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit_notes">备注</Label>
                  <Input
                    id="edit_notes"
                    value={editFormData.notes}
                    onChange={(e) => setEditFormData({ ...editFormData, notes: e.target.value })}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={updateSubscription.isPending}>
                  {updateSubscription.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  保存修改
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* #248：确认动作改为信息核对弹窗（完整记录 + 后端预览值），弹窗内二次确认才发起请求 */}
      <SubscriptionConfirmDialog
        open={confirmState?.action === "confirm" && !!confirmingSub}
        onOpenChange={(open) => !open && setConfirmState(null)}
        subscriptionId={confirmState?.action === "confirm" ? confirmState.id : null}
        subType={confirmingSub?.sub_type}
        investorNameMap={investorNameMap}
        platformNameMap={platformNameMap}
        isConfirming={confirmSubscription.isPending}
        onConfirm={() => {
          if (confirmState?.action !== "confirm") return;
          confirmSubscription.mutate(
            { id: confirmState.id },
            { onSuccess: () => setConfirmState(null) }
          );
        }}
      />

      <AlertDialog
        open={!!confirmState && confirmState.action !== "confirm"}
        onOpenChange={(open) => !open && setConfirmState(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmState ? CONFIRM_TEXT[confirmState.action].title : ""}</AlertDialogTitle>
            <AlertDialogDescription>{confirmState ? CONFIRM_TEXT[confirmState.action].desc : ""}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                runConfirm();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={editHint} onOpenChange={setEditHint}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>无法直接修改</AlertDialogTitle>
            <AlertDialogDescription>
              请先点击「取消确认」按钮（↩️图标），将申请状态改为 pending 后再修改
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction>知道了</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
