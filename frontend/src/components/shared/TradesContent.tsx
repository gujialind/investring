"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
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
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { formatCurrency, formatSharesUnit, formatNav, formatDate, formatProductName, toDateOnly, parseDateOnly, getStatusBadgeVariant, cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { TRADE_DIRECTION_COLORS } from "@/lib/colors";
import { Plus, ArrowLeft, CheckCircle, XCircle, Loader2, Pencil, Trash2, Undo, Filter, CalendarClock, Info } from "lucide-react";
import Link from "next/link";
import type { DateRange } from "react-day-picker";
import { isSameDay, subYears } from "date-fns";
import { ApiException } from "@/lib/api";
import type { TradeListParams } from "@/lib/api";
import type { Trade, TradeCreate, TradeUpdate } from "@/types/trade";
import {
  canEditArrivalDate,
  cashLegArrived,
  cashOrphanLabel,
  cashSubMeta,
  groupTradeRows,
  isCashLeg,
} from "@/lib/tradePairs";
import { applyBuyAmountLinkage, netFromActual, sellDerivedAmounts } from "@/lib/tradeAmounts";
import {
  useTradeList,
  useCreateTrade,
  useUpdateTrade,
  useConfirmTrade,
  useCancelTrade,
  useUnconfirmTrade,
  useDeleteTrade,
} from "@/hooks/useTrade";
import { usePlatformList } from "@/hooks/usePlatform";
import { useUIStore } from "@/stores/uiStore";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import PaginationBar from "@/components/shared/PaginationBar";
import NameCodeCell from "@/components/shared/NameCodeCell";
import ProductCell from "@/components/shared/ProductCell";
import DatePairCell from "@/components/shared/DatePairCell";
import ProductFilterSelect from "@/components/shared/ProductFilterSelect";
import SearchableProductSelect from "@/components/shared/SearchableProductSelect";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import ProductFormDialog from "@/components/shared/ProductFormDialog";
import { ProductSelection } from "@/components/shared/ProductFilterDialog";
import { TradeConfirmDialog } from "@/components/shared/TradeConfirmDialog";

interface TradesContentProps {
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

/**
 * 调仓 CASH 腿判定（#493 §3.4.5）：现金孤儿行不得露出调仓 CASH 生命周期操作按钮——
 * 这类腿只能由基金腿驱动，直接 confirm/unconfirm/cancel/delete 一律 `CASH_TRADE_FORBIDDEN`。
 * 前缀口径与 `cashOrphanLabel` 同源：12 位 hex = 现金转移组（独立生命周期，仍需按钮）、
 * `sub_` = 申赎现金腿（生命周期在申赎页）、其余 = 调仓。
 */
function isRebalCashLeg(trade: Trade): boolean {
  const g = trade.transfer_group ?? "";
  if (!isCashLeg(trade)) return false;
  if (g.startsWith("sub_") || /^[0-9a-f]{12}$/.test(g)) return false;
  return true;
}

const CONFIRM_TEXT: Record<ConfirmState extends infer S ? S extends { action: string } ? S["action"] : never : never, { title: string; desc: string }> = {
  confirm: { title: "确认交易", desc: "确定要确认该交易吗？" },
  cancel: { title: "取消交易", desc: "确定要取消该交易吗？" },
  unconfirm: { title: "取消确认", desc: "取消后可以修改或删除。是否继续？" },
  delete: { title: "删除交易", desc: "删除将同时删除配对的现金记录，且不可恢复。是否继续？" },
};

/** 默认交易日期区间 = 快捷项「近1年」（#126 决策⑤，区间语义与 DateRangePicker 快捷项一致） */
function defaultTradeRange(): DateRange {
  return { from: subYears(new Date(), 1), to: new Date() };
}

/** 与默认区间一致（isSameDay 双端比较）→ 视为「无筛选」默认态，用于重置按钮显隐与空态文案 */
function isDefaultTradeRange(range: DateRange | undefined): boolean {
  if (!range?.from || !range.to) return false;
  const d = defaultTradeRange();
  return !!d.from && !!d.to && isSameDay(range.from, d.from) && isSameDay(range.to, d.to);
}

/**
 * 调仓交易页内容（桌面/移动共用）。
 * 抽离自原 app/portfolio/[code]/trades/page.tsx，用 AlertDialog 替换原生 confirm/alert。
 * 桌面用表格；移动用可横向滚动表格（variant=mobile 时外层加 overflow-x-auto）。
 */
export default function TradesContent({ basePath, variant = "desktop" }: TradesContentProps) {
  const params = useParams();
  const code = params.code as string;

  // 筛选状态（#126 服务端筛选）：tradeRange 默认最近 1 年（决策⑤，惰性初始化避免每渲染重算）
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [tradeTypeFilter, setTradeTypeFilter] = useState<string | undefined>(undefined);
  // 产品多选筛选（#155）：undefined = 全部产品；元素为 {code, market}（market 可空串）
  const [productFilters, setProductFilters] = useState<ProductSelection[] | undefined>(undefined);
  const [platformFilter, setPlatformFilter] = useState<string | undefined>(undefined);
  const [tradeRange, setTradeRange] = useState<DateRange | undefined>(() => defaultTradeRange());
  const [confirmRange, setConfirmRange] = useState<DateRange | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filterOpen, setFilterOpen] = useState(false);

  // 空筛选字段为 undefined，axios 不传参；产品多选拼 `code|market` 逗号分隔（market 段可空，
  // 如 `CASH|`），与后端 products 参数契约一致（#155，与 product_code/market 互斥不同传）
  const listParams: TradeListParams = {
    portfolio_code: code,
    page,
    page_size: pageSize,
    status: statusFilter,
    trade_type: tradeTypeFilter,
    products: productFilters?.length
      ? productFilters.map((p) => `${p.code}|${p.market}`).join(",")
      : undefined,
    platform_code: platformFilter,
    trade_date_start: tradeRange?.from ? toDateOnly(tradeRange.from) : undefined,
    trade_date_end: tradeRange?.to ? toDateOnly(tradeRange.to) : undefined,
    confirm_date_start: confirmRange?.from ? toDateOnly(confirmRange.from) : undefined,
    confirm_date_end: confirmRange?.to ? toDateOnly(confirmRange.to) : undefined,
  };
  const { data, isLoading, isFetching } = useTradeList(listParams);
  const createTrade = useCreateTrade();
  const confirmTrade = useConfirmTrade();
  const cancelTrade = useCancelTrade();
  const unconfirmTrade = useUnconfirmTrade();
  const deleteTradeMutation = useDeleteTrade();
  const { data: platformsData } = usePlatformList({ page_size: 100 });

  const trades = data?.items || [];
  const total = data?.total ?? 0;
  const platforms = platformsData?.items || [];

  // 结对视图行（#126 决策⑧）：保持后端排序序，同组相邻时配对腿自然成对
  const tradeRows = groupTradeRows(trades);

  // 平台 name 映射（#124 模式）：平台列主次双行 + 现金子行「现金扣款/到账 · 平台名」复用
  const platformNameMap = useMemo(
    () => new Map((platformsData?.items ?? []).map((plat) => [plat.code, plat.name])),
    [platformsData?.items]
  );
  // 非默认筛选判定（默认集 = 仅 tradeRange 为最近 1 年）：驱动「重置」按钮显隐与空态文案
  const hasNonDefaultFilter =
    statusFilter !== undefined ||
    tradeTypeFilter !== undefined ||
    productFilters !== undefined ||
    platformFilter !== undefined ||
    confirmRange !== undefined ||
    tradeRange === undefined ||
    !isDefaultTradeRange(tradeRange);
  const activeFilterCount =
    (statusFilter ? 1 : 0) +
    (tradeTypeFilter ? 1 : 0) +
    (productFilters?.length ? 1 : 0) +
    (platformFilter ? 1 : 0) +
    (confirmRange ? 1 : 0) +
    (tradeRange === undefined || !isDefaultTradeRange(tradeRange) ? 1 : 0);

  const resetFilters = () => {
    setStatusFilter(undefined);
    setTradeTypeFilter(undefined);
    setProductFilters(undefined);
    setPlatformFilter(undefined);
    setTradeRange(defaultTradeRange());
    setConfirmRange(undefined);
    setPage(1);
  };

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  // 提交交易表单内嵌「新增产品」弹窗（受控，创建成功后自动选中）
  const [productFormOpen, setProductFormOpen] = useState(false);
  const [tradeType, setTradeType] = useState<"buy" | "sell">("buy");
  const [formData, setFormData] = useState({
    product_code: "",
    market: "",
    platform_code: "",
    cash_platform_code: "",
    shares: "",
    amount: "",
    net_amount: "",
    price: "",
    fee: "",
    trade_date: toDateOnly(new Date()),
  });
  // 买入金额联动锚点（#193）：记录最后手改的字段，fee 变化时按锚点重算另一字段
  const [amountAnchor, setAmountAnchor] = useState<"actual" | "net">("actual");
  const [confirmState, setConfirmState] = useState<ConfirmState>(null);
  // 编辑提示（confirmed 行原 alert 改为内部状态展示）
  const [editHint, setEditHint] = useState(false);
  // pending 交易编辑（#174）：editingTrade 非空即打开编辑 Dialog
  const [editingTrade, setEditingTrade] = useState<Trade | null>(null);
  const [editFormData, setEditFormData] = useState({
    amount: "",
    net_amount: "",
    shares: "",
    price: "",
    fee: "",
    trade_date: toDateOnly(new Date()),
    notes: "",
  });
  const [editAmountAnchor, setEditAmountAnchor] = useState<"actual" | "net">("actual");
  // 已确认卖出的窄例外（#493 §3.4.5）：只开放「到账日期 + 备注」，
  // 其他财务字段仍走「先取消确认」提示（与后端 PUT 的字段白名单一一对应）
  const [arrivalTrade, setArrivalTrade] = useState<Trade | null>(null);
  const [arrivalFormData, setArrivalFormData] = useState({ cash_confirm_date: "", notes: "" });
  // 顶层无条件调用（hooks 规则）；id=0 时 mutate 不会被触发（Dialog 打开时必有 id）
  const editTradeId = editingTrade?.id ?? arrivalTrade?.id ?? 0;
  const updateTrade = useUpdateTrade(editTradeId);
  // 命中 DUPLICATE_TRADE 时暂存待重试的交易，由确认框引导 allow_duplicate 重试
  const [duplicateTrade, setDuplicateTrade] = useState<TradeCreate | null>(null);
  const addToast = useUIStore((state) => state.addToast);

  const resetTradeForm = () => {
    setIsDialogOpen(false);
    setFormData({ product_code: "", market: "", platform_code: "", cash_platform_code: "", shares: "", amount: "", net_amount: "", price: "", fee: "", trade_date: toDateOnly(new Date()) });
    setAmountAnchor("actual");
  };

  // 买入金额双字段联动（#193）：手改字段保留原始输入并记锚点，另一字段回填埋入量化派生值；
  // fee 变更按锚点重算。创建与编辑表单同构，仅状态载体不同
  const onBuyFieldChange = (changed: "actual" | "net", value: string) => {
    setAmountAnchor(changed);
    const fields = changed === "actual"
      ? { actual: value, net: formData.net_amount, fee: formData.fee }
      : { actual: formData.amount, net: value, fee: formData.fee };
    const r = applyBuyAmountLinkage(changed, changed, fields);
    setFormData({ ...formData, amount: r.actual, net_amount: r.net });
  };

  const onBuyFeeChange = (value: string) => {
    const r = applyBuyAmountLinkage("fee", amountAnchor, {
      actual: formData.amount,
      net: formData.net_amount,
      fee: value,
    });
    setFormData({ ...formData, fee: value, amount: r.actual, net_amount: r.net });
  };

  const onEditBuyFieldChange = (changed: "actual" | "net", value: string) => {
    setEditAmountAnchor(changed);
    const fields = changed === "actual"
      ? { actual: value, net: editFormData.net_amount, fee: editFormData.fee }
      : { actual: editFormData.amount, net: value, fee: editFormData.fee };
    const r = applyBuyAmountLinkage(changed, changed, fields);
    setEditFormData({ ...editFormData, amount: r.actual, net_amount: r.net });
  };

  const onEditBuyFeeChange = (value: string) => {
    const r = applyBuyAmountLinkage("fee", editAmountAnchor, {
      actual: editFormData.amount,
      net: editFormData.net_amount,
      fee: value,
    });
    setEditFormData({ ...editFormData, fee: value, amount: r.actual, net_amount: r.net });
  };

  // 派生量与阻断校验（#193）：买入净额 ≤ 0 阻断提交；卖出有价时展示毛额/到手、到手 ≤ 0 阻断
  //（镜像后端 trade_service 口径：买入净额=实付−手续费，卖出到手=quantize(份额×价格)−手续费）
  const createBuyNet = netFromActual(formData.amount, formData.fee);
  const createBuyNetInvalid = tradeType === "buy" && createBuyNet !== null && createBuyNet <= 0;
  const createSellDerived =
    tradeType === "sell"
      ? sellDerivedAmounts(formData.shares, formData.price, formData.fee)
      : null;
  const createSellNetInvalid =
    tradeType === "sell" && createSellDerived !== null && createSellDerived.actualReceived <= 0;
  const editBuyNet = netFromActual(editFormData.amount, editFormData.fee);
  const editBuyNetInvalid =
    editingTrade?.trade_type === "buy" && editBuyNet !== null && editBuyNet <= 0;
  const editSellDerived =
    editingTrade?.trade_type === "sell"
      ? sellDerivedAmounts(editFormData.shares, editFormData.price, editFormData.fee)
      : null;
  const editSellNetInvalid =
    editingTrade?.trade_type === "sell" &&
    editSellDerived !== null &&
    editSellDerived.actualReceived <= 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.platform_code) {
      // 原生 select 替换为自定义组件后浏览器校验失效，须手动拦截（与 SubscriptionsContent 同口径）
      addToast({ type: "error", title: "表单校验失败", message: "请选择平台" });
      return;
    }
    const payload: TradeCreate = {
      portfolio_code: code,
      product_code: formData.product_code,
      market: formData.market || undefined,
      platform_code: formData.platform_code || undefined,
      // 扣款平台仅买入可传（#493）：卖出到账平台改在确认弹窗录入，创建期传会被后端
      // 以 CASH_PLATFORM_NOT_ALLOWED 拒绝，故此处按方向直接不发
      cash_platform_code:
        tradeType === "buy" ? formData.cash_platform_code || undefined : undefined,
      trade_type: tradeType,
      trade_date: formData.trade_date,
      price: formData.price ? parseFloat(formData.price) : undefined,
      fee: formData.fee ? parseFloat(formData.fee) : 0,
      ...(tradeType === "buy"
        ? { amount: parseFloat(formData.amount) }
        : { shares: parseFloat(formData.shares) }),
    };
    createTrade.mutate(payload, {
      onSuccess: resetTradeForm,
      onError: (error: unknown) => {
        // 重复交易：弹确认框引导 allow_duplicate 重试（hook 层已抑制该错误码的 toast）
        if (error instanceof ApiException && error.code === "DUPLICATE_TRADE") {
          setDuplicateTrade(payload);
        }
      },
    });
  };

  // 打开编辑 Dialog 并按方向预填（#174）：买入预填金额、卖出预填份额
  // 买入预填双维度（#193）：实际=actual_amount（#182 D1 含费口径）、净投入=amount，
  // 两持久化值差恒为 fee（后端不变量），锚点默认实际字段
  const openEditDialog = (trade: Trade) => {
    setEditingTrade(trade);
    setEditAmountAnchor("actual");
    setEditFormData({
      amount:
        trade.trade_type === "buy" ? String(trade.actual_amount ?? trade.amount ?? "") : "",
      net_amount:
        trade.trade_type === "buy" && trade.amount != null ? String(trade.amount) : "",
      shares: trade.trade_type === "sell" ? String(trade.shares ?? "") : "",
      price: trade.price != null ? String(trade.price) : "",
      fee: trade.fee ? String(trade.fee) : "",
      trade_date: trade.trade_date,
      notes: trade.notes ?? "",
    });
  };

  const handleEditSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingTrade) return;
    // 仅组装非空字段：空串不入 payload（exclude_unset 语义下避免误清）
    const payload: TradeUpdate = {};
    if (editingTrade.trade_type === "buy" && editFormData.amount) {
      payload.amount = parseFloat(editFormData.amount);
    }
    if (editingTrade.trade_type === "sell" && editFormData.shares) {
      payload.shares = parseFloat(editFormData.shares);
    }
    if (editFormData.price) payload.price = parseFloat(editFormData.price);
    if (editFormData.fee) payload.fee = parseFloat(editFormData.fee);
    if (editFormData.trade_date) payload.trade_date = editFormData.trade_date;
    if (editFormData.notes) payload.notes = editFormData.notes;
    // 失败时 hook 已 toast，Dialog 保持打开可重试（不挂 onError 关闭逻辑）
    updateTrade.mutate(payload, { onSuccess: () => setEditingTrade(null) });
  };

  // 已确认卖出的窄表单（#493 §3.4.5）：只提交 cash_confirm_date + notes 子集，
  // 与后端 PUT 的窄例外一致（混入其他字段整体拒绝 → 故此处绝不带其他财务字段）
  const openArrivalDialog = (trade: Trade) => {
    setArrivalTrade(trade);
    setArrivalFormData({
      cash_confirm_date: trade.cash_confirm_date ?? "",
      notes: trade.notes ?? "",
    });
  };

  const handleArrivalSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!arrivalTrade || !arrivalFormData.cash_confirm_date) return;
    const payload: TradeUpdate = { cash_confirm_date: arrivalFormData.cash_confirm_date };
    // 备注只在有值时随行：空串不入 payload（exclude_unset 语义下避免误清）
    if (arrivalFormData.notes) payload.notes = arrivalFormData.notes;
    updateTrade.mutate(payload, { onSuccess: () => setArrivalTrade(null) });
  };

  // confirm 动作由 TradeConfirmDialog 内部触发（#248），此处仅处理其余三个动作
  // （#493：三个动作都是**整组**口径，故 portfolio_code 从 mutation variables 传，
  //   不依赖响应里不存在的字段——cancel/unconfirm 只回 {message}、delete 无响应体）
  const runConfirm = () => {
    if (!confirmState) return;
    const { action, id } = confirmState;
    if (action === "cancel") cancelTrade.mutate({ id, portfolioCode: code });
    else if (action === "unconfirm") unconfirmTrade.mutate({ id, portfolioCode: code });
    else if (action === "delete") deleteTradeMutation.mutate({ id, portfolioCode: code });
    setConfirmState(null);
  };

  // #248 确认信息弹窗派生：被确认交易取自当前页列表（含读侧 product_name）；
  // #493：现金平台/到账日一律读基金腿的读侧派生字段（后端按配对 CASH 腿批量派生），
  // 不再从当前页里寻找配对 CASH 行——分页/状态筛选拆散或半确认组时同样完整
  const confirmingTrade =
    confirmState?.action === "confirm"
      ? trades.find((t) => t.id === confirmState.id) ?? null
      : null;
  // 行掉出当前列表时（refetch/翻页/他端确认）同步清空 confirmState：open 以「行在
  // 列表中」门控属被动关闭（onOpenChange 不触发），不清 state 行重现时弹窗会自发重开
  useEffect(() => {
    if (confirmState?.action === "confirm" && !confirmingTrade) {
      setConfirmState(null);
    }
  }, [confirmState, confirmingTrade]);

  // 筛选栏控件（visual-spec §9）：顺序 = 交易日期区间 → 确认日期区间 → 状态 → 产品 → 平台 → 类型；
  // 控件统一 h-9，下拉走 ui/select（「全部 X」用 "all" 哨兵，Radix SelectItem 不允许空串值）；
  // 平台为 SearchablePlatformSelect（Popover，null 哨兵）；
  // 产品为 ProductFilterDialog 多选弹窗触发按钮（#155），outline 风格同筛选栏
  const rangeWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[240px]";
  const selectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[150px]";
  const productSelectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[220px]";
  const filterControls = (
    <>
      <DateRangePicker
        value={tradeRange}
        onChange={(r) => {
          setTradeRange(r);
          setPage(1);
        }}
        placeholder="交易日期"
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
      <ProductFilterSelect
        variant={variant}
        value={productFilters ?? []}
        onChange={(selection) => {
          // 空选择归一为 undefined（= 全部产品），与非默认筛选判定口径一致
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
      <Select
        value={tradeTypeFilter ?? "all"}
        onValueChange={(v) => {
          setTradeTypeFilter(v === "all" ? undefined : v);
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="全部类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部类型</SelectItem>
          <SelectItem value="buy">买入</SelectItem>
          <SelectItem value="sell">卖出</SelectItem>
        </SelectContent>
      </Select>
      {hasNonDefaultFilter && (
        <Button variant="ghost" size="sm" className="h-9" onClick={resetFilters}>
          重置
        </Button>
      )}
    </>
  );

  // 主行（普通单行 / 结对主行共用）；结对主行去下边框使主+子视觉成组（规范 §8 结对行）
  const renderMainRow = (trade: Trade, isPairMain: boolean) => (
    <TableRow key={trade.id} className={isPairMain ? "border-b-0" : undefined}>
      <TableCell>
        {/* CASH 孤儿单行：产品列改显示业务来源（§5.8 简化口径）；结对主行/基金单行走 ProductCell 双行 */}
        {trade.product_code === "CASH" && !isPairMain ? (
          <span className="text-sm">{cashOrphanLabel(trade)}</span>
        ) : (
          <ProductCell code={trade.product_code} name={trade.product_name} market={trade.market} />
        )}
      </TableCell>
      <TableCell>
        {trade.platform_code ? <NameCodeCell code={trade.platform_code} nameMap={platformNameMap} /> : "--"}
      </TableCell>
      <TableCell>
        {/* 方向标识无状态语义：neutral badge + 方向色圆点（lib/colors，#127） */}
        <Badge variant="neutral">
          <span
            className="mr-1.5 h-1.5 w-1.5 rounded-full"
            style={{ background: TRADE_DIRECTION_COLORS[trade.trade_type === "buy" ? "buy" : "sell"] }}
          />
          {trade.trade_type === "buy" ? "买入" : "卖出"}
        </Badge>
      </TableCell>
      {/* 金额=净额 amount（#173 口径：买入 金额+手续费=实扣、卖出 金额−手续费=实到）；
          真 0 正常显示 ¥0.00 / 0.00 份，空值由 format 函数 fallback 出 --（#249） */}
      <TableCell className="number-cell">
        {formatCurrency(trade.amount)}
      </TableCell>
      <TableCell className="number-cell">
        {formatSharesUnit(trade.shares)}
      </TableCell>
      <TableCell className="number-cell">
        {formatCurrency(trade.fee)}
      </TableCell>
      <TableCell className="number-cell">{formatNav(trade.price)}</TableCell>
      <TableCell>
        {/* 成对日期合并单列（#355）：上行交易日、下行确认日；pending 的确认日是预计值，下行内联标注 */}
        <DatePairCell
          topLabel="交易日期"
          topValue={formatDate(trade.trade_date)}
          bottomLabel="确认日期"
          bottomValue={trade.confirm_date ? formatDate(trade.confirm_date) : "--"}
          estimated={trade.status === "pending" && !!trade.confirm_date}
        />
      </TableCell>
      <TableCell>
        <Badge variant={getStatusBadgeVariant(trade.status)}>
          {trade.status === "confirmed" ? "已确认" : trade.status === "pending" ? "待确认" : "已取消"}
        </Badge>
      </TableCell>
      <TableCell className="text-right">
        {/* 操作按钮对齐后端允许矩阵（#176）：pending=确认/删除（场内无取消，
            与后端 cancel_trade 的 CANNOT_CANCEL_EXCHANGE 一致）；confirmed=取消确认/修改引导，无删除。
            #493：调仓 CASH 腿（含孤儿行）不露出任何生命周期按钮——后端对其
            confirm/unconfirm/cancel/delete 一律 CASH_TRADE_FORBIDDEN，唯一驱动入口是基金腿 */}
        {isRebalCashLeg(trade) ? null : (
          <>
            {trade.status === "pending" && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => openEditDialog(trade)}
                  title="编辑"
                >
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmState({ action: "confirm", id: trade.id })}
                  disabled={confirmTrade.isPending}
                  title="确认"
                >
                  <CheckCircle className="h-4 w-4" />
                </Button>
                {trade.market !== "CN_EXCHANGE" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmState({ action: "cancel", id: trade.id })}
                    disabled={cancelTrade.isPending}
                    title="取消"
                  >
                    <XCircle className="h-4 w-4" />
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmState({ action: "delete", id: trade.id })}
                  disabled={deleteTradeMutation.isPending}
                  title="删除"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </>
            )}
            {trade.status === "confirmed" && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmState({ action: "unconfirm", id: trade.id })}
                  title="取消确认"
                >
                  <Undo className="h-4 w-4" />
                </Button>
                {canEditArrivalDate(trade) ? (
                  // 已确认**基金**卖出的窄例外（#493）：只开放到账日期 + 备注，其他财务字段
                  // 仍先取消确认。CASH 卖出腿（赎回 sub_ 腿 / 现金转移主腿）走 #525 门控排除：
                  // 它们没有可改的到账日语义（后端 PUT 一律 CASH_TRADE_FORBIDDEN，读侧派生
                  // 的 cash_confirm_date 恒 null），露出按钮即死路入口
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openArrivalDialog(trade)}
                    title="修改到账日期"
                  >
                    <CalendarClock className="h-4 w-4" />
                  </Button>
                ) : (
                  <Button variant="ghost" size="sm" onClick={() => setEditHint(true)} title="修改">
                    <Pencil className="h-4 w-4" />
                  </Button>
                )}
              </>
            )}
          </>
        )}
      </TableCell>
    </TableRow>
  );

  // 现金子行（规范 §8）：首列 pl-8、整行 bg-muted/50、内容 text-xs；金额 text-foreground 手工 +/- 前缀
  // （资金流向非涨跌语义，禁用 gain/loss token）；操作列空、不单独响应 hover
  const renderCashSubRow = (main: Trade, sub: Trade) => {
    // #493 + 评审加固：现金腿生效日未到、或生效日已到但**仍未 confirmed**（跨天转移的
    // 转入腿到期未确认时是 pending、不计入可用现金）→ 标注「现金待到账」——状态列显示的是
    // 主行（基金腿）的已确认，故须在子行显式区分，避免把尚未到账的现金标成已经到账
    const meta = cashSubMeta(main, { arrived: cashLegArrived(sub) });
    const platformName = sub.platform_code ? platformNameMap.get(sub.platform_code) : undefined;
    return (
      <TableRow key={`cash-${sub.id}`} className="bg-muted/50 hover:bg-muted/50">
        <TableCell className="pl-8">
          <span className="text-xs text-muted-foreground">
            {meta.label}
            {platformName ? ` · ${platformName}` : ""}
          </span>
        </TableCell>
        {/* 空占位以 colSpan 折叠（#355）：主行 10 列 = 产品·平台·类型·金额·份额·手续费·价格·日期·状态·操作，
            子行 = 标签(1) + 平台·类型(2) + 金额(1) + 份额·手续费·价格(3) + 生效日(1) + 状态·操作(2) = 10。
            子行槽位纯位置耦合、tsc/lint 拦不住，折叠后 span 写错会立刻在视觉上暴露而非静默错一列 */}
        <TableCell colSpan={2} />
        <TableCell className="number-cell">
          <span className="text-xs text-foreground">
            {meta.sign}
            {formatCurrency(sub.amount ?? 0)}
          </span>
        </TableCell>
        <TableCell colSpan={3} />
        {/* 现金腿生效日（#493）：买入=扣款日 T、卖出=到账日 A；与主行「交易/确认日期」同列对齐 */}
        <TableCell>
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {sub.confirm_date ? formatDate(sub.confirm_date) : ""}
          </span>
        </TableCell>
        <TableCell colSpan={2} />
      </TableRow>
    );
  };

  if (isLoading) return <LoadingState />;

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
            <h1 className="text-2xl font-semibold">
              调仓交易
            </h1>
            <p className="text-muted-foreground">组合代码: {code}</p>
          </div>
        </div>
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen} modal={false}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              提交交易
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>提交交易</DialogTitle>
              <DialogDescription>提交买入或卖出交易</DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit}>
              <div className="space-y-4 py-4">
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant={tradeType === "buy" ? "default" : "outline"}
                    onClick={() => setTradeType("buy")}
                    className="flex-1"
                  >
                    买入
                  </Button>
                  <Button
                    type="button"
                    variant={tradeType === "sell" ? "default" : "outline"}
                    onClick={() => setTradeType("sell")}
                    className="flex-1"
                  >
                    卖出
                  </Button>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="product_code">产品</Label>
                  {/* 可搜索单选下拉（粒度 code|market，消除按 code 猜测市场的歧义）+ 新建入口 */}
                  <div className="flex gap-2">
                    <div className="min-w-0 flex-1">
                      <SearchableProductSelect
                        id="product_code"
                        value={
                          formData.product_code
                            ? { code: formData.product_code, market: formData.market }
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
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      className="h-10 w-10 shrink-0"
                      aria-label="新增产品"
                      title="新增产品"
                      onClick={() => setProductFormOpen(true)}
                    >
                      <Plus className="h-4 w-4" />
                    </Button>
                  </div>
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
                {/* 现金平台只在买入侧出现（#493）：买入=扣款平台、创建即扣款；
                    卖出的到账平台与到账日一律在确认弹窗录入（创建期传入后端直接拒绝） */}
                {tradeType === "buy" && (
                  <div className="space-y-2">
                    <Label htmlFor="cash_platform_code">扣款平台（可选）</Label>
                    <SearchablePlatformSelect
                      platforms={platforms}
                      value={formData.cash_platform_code || null}
                      onChange={(v) => setFormData({ ...formData, cash_platform_code: v ?? "" })}
                      specialOptionLabel="同交易平台"
                      id="cash_platform_code"
                    />
                  </div>
                )}
                {/* 买入创建即记账（#493 决策 1）：扣款腿当场 confirmed、基金份额待确认。
                    不写清楚会让用户误以为「提交≠扣钱」而重复下单 */}
                {tradeType === "buy" && (
                  <Alert data-testid="buy-deduct-hint">
                    <Info className="h-4 w-4" />
                    <AlertDescription>
                      提交即记扣款：现金按所选扣款平台当场扣减，基金份额仍待确认（等待确认日）。
                    </AlertDescription>
                  </Alert>
                )}
                {tradeType === "sell" && (
                  <Alert data-testid="sell-arrival-hint">
                    <Info className="h-4 w-4" />
                    <AlertDescription>
                      卖出到账平台与到账日在确认时录入，缺省为到账日=基金确认日、平台=交易平台。
                    </AlertDescription>
                  </Alert>
                )}
                {tradeType === "buy" ? (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="amount">实际支付金额（含费，元）</Label>
                      <Input
                        id="amount"
                        type="number"
                        step="0.01"
                        value={formData.amount}
                        onChange={(e) => onBuyFieldChange("actual", e.target.value)}
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="net_amount">净投入金额（扣费后，元）</Label>
                      <Input
                        id="net_amount"
                        type="number"
                        step="0.01"
                        value={formData.net_amount}
                        onChange={(e) => onBuyFieldChange("net", e.target.value)}
                        required
                      />
                      <p className="text-xs text-muted-foreground">
                        净投入 = 实付 − 手续费，双向自动联动；提交以实付（含费）为准
                      </p>
                    </div>
                    {createBuyNetInvalid && (
                      <Alert variant="destructive">
                        <AlertDescription>
                          净投入金额需大于 0：手续费不能不小于实际支付金额
                        </AlertDescription>
                      </Alert>
                    )}
                  </>
                ) : (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="shares">份额</Label>
                      <Input
                        id="shares"
                        type="number"
                        step="0.01"
                        value={formData.shares}
                        onChange={(e) => setFormData({ ...formData, shares: e.target.value })}
                        required
                      />
                    </div>
                    {/* 卖出金额为纯派生量（#190）：只读展示毛额/到手，与后端落库口径一致；场外未填价不展示 */}
                    {createSellDerived && (
                      <div className="space-y-1 rounded-md bg-muted p-3 text-sm">
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">毛额（份额×价格）</span>
                          <span>{formatCurrency(createSellDerived.gross)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">实际到账（毛额−手续费）</span>
                          <span>{formatCurrency(createSellDerived.actualReceived)}</span>
                        </div>
                      </div>
                    )}
                    {createSellNetInvalid && (
                      <Alert variant="destructive">
                        <AlertDescription>
                          实际到账需大于 0：手续费不能不小于卖出毛额
                        </AlertDescription>
                      </Alert>
                    )}
                  </>
                )}
                <div className="space-y-2">
                  <Label htmlFor="price">价格</Label>
                  <Input
                    id="price"
                    type="number"
                    step="0.0001"
                    value={formData.price}
                    onChange={(e) => setFormData({ ...formData, price: e.target.value })}
                    placeholder="可选，确认时填写"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fee">手续费（元）</Label>
                  <Input
                    id="fee"
                    type="number"
                    step="0.01"
                    value={formData.fee}
                    onChange={(e) =>
                      tradeType === "buy"
                        ? onBuyFeeChange(e.target.value)
                        : setFormData({ ...formData, fee: e.target.value })
                    }
                    placeholder="默认 0"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="trade_date">交易日期</Label>
                  <DatePicker
                    id="trade_date"
                    date={parseDateOnly(formData.trade_date)}
                    onSelect={(date) => {
                      setFormData({ ...formData, trade_date: toDateOnly(date) });
                    }}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  type="submit"
                  disabled={
                    createTrade.isPending ||
                    !formData.product_code ||
                    createBuyNetInvalid ||
                    createSellNetInvalid
                  }
                >
                  {createTrade.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  提交交易
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>交易记录</CardTitle>
          <CardDescription>买入和卖出记录</CardDescription>
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
                  <TableHead>产品</TableHead>
                  <TableHead>平台</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead className="number-cell">金额</TableHead>
                  <TableHead className="number-cell">份额</TableHead>
                  <TableHead className="number-cell">手续费</TableHead>
                  <TableHead className="number-cell">价格</TableHead>
                  <TableHead>交易/确认日期</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tradeRows.map((row) =>
                  row.kind === "pair" ? (
                    <Fragment key={row.main.id}>
                      {renderMainRow(row.main, true)}
                      {renderCashSubRow(row.main, row.sub)}
                    </Fragment>
                  ) : (
                    renderMainRow(row.trade, false)
                  )
                )}
              </TableBody>
            </Table>
          </div>
          {/* 空态：默认筛选集下为空 = 暂无记录；非默认筛选下为空 = 引导重置（规范 §8 变体②） */}
          {trades.length === 0 &&
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
              <EmptyState message="暂无交易记录" />
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

      {/* pending 交易编辑 Dialog（#174）：产品/平台/方向后端不支持改，只读展示 */}
      <Dialog open={!!editingTrade} onOpenChange={(open) => !open && setEditingTrade(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑交易</DialogTitle>
            <DialogDescription>仅待确认交易可编辑，产品/平台/方向不可修改</DialogDescription>
          </DialogHeader>
          {editingTrade && (
            <form onSubmit={handleEditSubmit}>
              <div className="space-y-4 py-4">
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">产品</span>
                    <span>
                      {formatProductName(editingTrade.product_name, editingTrade.product_code)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">平台</span>
                    <span>
                      {editingTrade.platform_code
                        ? `${platformNameMap.get(editingTrade.platform_code) ?? editingTrade.platform_code}（${editingTrade.platform_code}）`
                        : "--"}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">方向</span>
                    <Badge variant="neutral">
                      <span
                        className="mr-1.5 h-1.5 w-1.5 rounded-full"
                        style={{ background: TRADE_DIRECTION_COLORS[editingTrade.trade_type === "buy" ? "buy" : "sell"] }}
                      />
                      {editingTrade.trade_type === "buy" ? "买入" : "卖出"}
                    </Badge>
                  </div>
                </div>
                {editingTrade.trade_type === "buy" ? (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="edit_amount">实际支付金额（含费，元）</Label>
                      <Input
                        id="edit_amount"
                        type="number"
                        step="0.01"
                        value={editFormData.amount}
                        onChange={(e) => onEditBuyFieldChange("actual", e.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="edit_net_amount">净投入金额（扣费后，元）</Label>
                      <Input
                        id="edit_net_amount"
                        type="number"
                        step="0.01"
                        value={editFormData.net_amount}
                        onChange={(e) => onEditBuyFieldChange("net", e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground">
                        净投入 = 实付 − 手续费，双向自动联动；保存以实付（含费）为准
                      </p>
                    </div>
                    {editBuyNetInvalid && (
                      <Alert variant="destructive">
                        <AlertDescription>
                          净投入金额需大于 0：手续费不能不小于实际支付金额
                        </AlertDescription>
                      </Alert>
                    )}
                  </>
                ) : (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="edit_shares">份额</Label>
                      <Input
                        id="edit_shares"
                        type="number"
                        step="0.01"
                        value={editFormData.shares}
                        onChange={(e) => setEditFormData({ ...editFormData, shares: e.target.value })}
                      />
                    </div>
                    {editSellDerived && (
                      <div className="space-y-1 rounded-md bg-muted p-3 text-sm">
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">毛额（份额×价格）</span>
                          <span>{formatCurrency(editSellDerived.gross)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">实际到账（毛额−手续费）</span>
                          <span>{formatCurrency(editSellDerived.actualReceived)}</span>
                        </div>
                      </div>
                    )}
                    {editSellNetInvalid && (
                      <Alert variant="destructive">
                        <AlertDescription>
                          实际到账需大于 0：手续费不能不小于卖出毛额
                        </AlertDescription>
                      </Alert>
                    )}
                  </>
                )}
                <div className="space-y-2">
                  <Label htmlFor="edit_price">价格</Label>
                  <Input
                    id="edit_price"
                    type="number"
                    step="0.0001"
                    value={editFormData.price}
                    onChange={(e) => setEditFormData({ ...editFormData, price: e.target.value })}
                    placeholder="可选，确认时填写"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit_fee">手续费（元）</Label>
                  <Input
                    id="edit_fee"
                    type="number"
                    step="0.01"
                    value={editFormData.fee}
                    onChange={(e) =>
                      editingTrade.trade_type === "buy"
                        ? onEditBuyFeeChange(e.target.value)
                        : setEditFormData({ ...editFormData, fee: e.target.value })
                    }
                    placeholder="默认 0"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit_trade_date">交易日期</Label>
                  <DatePicker
                    id="edit_trade_date"
                    date={parseDateOnly(editFormData.trade_date)}
                    onSelect={(date) => {
                      setEditFormData({ ...editFormData, trade_date: toDateOnly(date) });
                    }}
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
                <Button
                  type="submit"
                  disabled={updateTrade.isPending || editBuyNetInvalid || editSellNetInvalid}
                >
                  {updateTrade.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  保存修改
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* 已确认卖出的窄表单（#493 §3.4.5）：只改到账日期（+备注），
          其他财务字段仍走「先取消确认」——与后端 PUT 的窄例外字段白名单一一对应 */}
      <Dialog open={!!arrivalTrade} onOpenChange={(open) => !open && setArrivalTrade(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改到账日期</DialogTitle>
            <DialogDescription>
              仅可修改到账日期与备注；其他字段请先「取消确认」再修改
            </DialogDescription>
          </DialogHeader>
          {arrivalTrade && (
            <form onSubmit={handleArrivalSubmit}>
              <div className="space-y-4 py-4">
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">产品</span>
                    <span>
                      {formatProductName(arrivalTrade.product_name, arrivalTrade.product_code)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">份额 / 到手金额</span>
                    <span>
                      {formatSharesUnit(arrivalTrade.shares)} / {formatCurrency(arrivalTrade.actual_amount)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">确认日期</span>
                    <span>{arrivalTrade.confirm_date ? formatDate(arrivalTrade.confirm_date) : "--"}</span>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="arrival_cash_confirm_date">到账日期</Label>
                  <DatePicker
                    id="arrival_cash_confirm_date"
                    date={parseDateOnly(arrivalFormData.cash_confirm_date)}
                    onSelect={(date) =>
                      setArrivalFormData({ ...arrivalFormData, cash_confirm_date: toDateOnly(date) })
                    }
                    showTradingDays
                    // #525：A 早于基金确认日 C 后端必 422（INVALID_DATE_ORDER），
                    // 与确认弹窗同口径提前到选择时硬禁用；C 缺失（异常数据）时不加下界、不臆造
                    dayDisabled={(day) =>
                      !!arrivalTrade.confirm_date && toDateOnly(day) < arrivalTrade.confirm_date
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    到账日之前该笔资金计入「卖出在途」，不计入可用现金；组内确认日及之后已有快照时须先删除快照。
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="arrival_notes">备注</Label>
                  <Input
                    id="arrival_notes"
                    value={arrivalFormData.notes}
                    onChange={(e) =>
                      setArrivalFormData({ ...arrivalFormData, notes: e.target.value })
                    }
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  type="submit"
                  disabled={updateTrade.isPending || !arrivalFormData.cash_confirm_date}
                >
                  {updateTrade.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  保存修改
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* #248：确认动作改为信息核对弹窗（完整记录 + 后端预览值），弹窗内二次确认才发起请求。
          #493：到账日期/到账平台在弹窗内录入，由弹窗回传有效选项（缺省不传 → 后端取 A=C、同基金平台） */}
      <TradeConfirmDialog
        open={confirmState?.action === "confirm" && !!confirmingTrade}
        onOpenChange={(open) => !open && setConfirmState(null)}
        trade={confirmingTrade}
        platforms={platforms}
        isConfirming={confirmTrade.isPending}
        onConfirm={(params) => {
          if (confirmState?.action !== "confirm") return;
          confirmTrade.mutate(
            { id: confirmState.id, params },
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
              请先点击「取消确认」按钮（↩️图标），将交易状态改为 pending 后再修改
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction>知道了</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* DUPLICATE_TRADE 确认重试 */}
      <AlertDialog open={!!duplicateTrade} onOpenChange={(open) => !open && setDuplicateTrade(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>检测到重复交易</AlertDialogTitle>
            <AlertDialogDescription>
              存在同组合/产品/方向/交易日且金额或份额相同的交易，是否仍要提交？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (duplicateTrade) {
                  createTrade.mutate(
                    { ...duplicateTrade, allow_duplicate: true },
                    { onSuccess: resetTradeForm }
                  );
                }
                setDuplicateTrade(null);
              }}
            >
              仍要提交
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* 新增产品：创建成功后自动选中（列表缓存失效由 hook 自带） */}
      <ProductFormDialog
        open={productFormOpen}
        onOpenChange={setProductFormOpen}
        onSuccess={(p) =>
          setFormData((prev) => ({ ...prev, product_code: p.code, market: p.market ?? "" }))
        }
      />
    </div>
  );
}
