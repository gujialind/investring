"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { subMonths, subYears } from "date-fns";
import { ArrowLeft, CheckCircle, XCircle, RefreshCw, Loader2, Pencil, Trash2, Ban } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DatePicker } from "@/components/ui/date-picker";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import ProductFormDialog from "@/components/shared/ProductFormDialog";
import QueryErrorState from "./QueryErrorState";
import ProductNavCards from "./ProductNavCards";
import {
  useProduct,
  useSyncProductHistory,
  useUpdateProduct,
  useDeleteProduct,
} from "@/hooks/useProduct";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import { ApiException } from "@/lib/api";
import {
  formatDateTime,
  formatMarketName,
  formatProductCodeLabel,
  toDateOnly,
} from "@/lib/utils";

// 虚拟产品判定（#514/#327）：对齐后端 product_service.VIRTUAL_PRODUCT_TYPES——无净值、不可交易，
// 故不渲染净值三卡与同步操作（与 ProductsContent 同一口径）。
const VIRTUAL_PRODUCT_TYPES = ["CASH", "IN_TRANSIT"];

// 同步历史周期（#856）：6 档预设 + 自定义开始日期，默认成立以来。除「成立以来」外
// 恒显式传 end_date=今天；「成立以来」不发请求体（后端 end 缺省补今天、start 无下界）。
const PERIOD_OPTIONS = [
  { value: "1m", label: "近1月" },
  { value: "3m", label: "近3月" },
  { value: "6m", label: "近6月" },
  { value: "1y", label: "近1年" },
  { value: "3y", label: "近3年" },
  { value: "all", label: "成立以来" },
  { value: "custom", label: "自定义" },
] as const;
type Period = (typeof PERIOD_OPTIONS)[number]["value"];

const DEACTIVATE_HINT =
  "停用后不可再新建该产品的买入/卖出记录（存量待确认记录不受影响）；清盘分配等持仓了结可经份额变动事件登记。";

interface ProductManageDetailContentProps {
  /** 链接前缀（§1.2 契约）：桌面 ""、移动 "/m" */
  basePath: "" | "/m";
  variant: "desktop" | "mobile";
}

/** 基本信息 / 分类维度卡的标签-值行（值缺失走 `--`，不折成误导性 0） */
function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="text-sm font-medium">{value}</div>
    </div>
  );
}

/**
 * #824 产品管理-净值型产品详情页（双端共享）：从产品管理表格点击产品名进入，
 * 路由 /products/{code}?market={market}（移动端 /m 前缀）。
 * 概览：基本信息卡（元信息）+ 分类维度卡（五维码值经字典映射中文名）+ 同步操作区（同步价格/历史），
 * 并复用 ProductNavCards 的净值三卡（累计净值走势 / 区间收益率 / 历史净值）——与组合详情-产品
 * 详情卡同一组件、同一口径。数据源：GET /products/{code}/{market}（useProduct）、维度字典、
 * nav-analysis/nav-history（经 ProductNavCards）。虚拟产品（CASH/IN_TRANSIT）隐藏净值三卡与同步。
 */
export default function ProductManageDetailContent({ basePath, variant }: ProductManageDetailContentProps) {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const code = params.code as string;
  const market = searchParams.get("market") ?? "";
  const isMobile = variant === "mobile";

  const { data: product, isLoading, isError, error, refetch } = useProduct(code, market);
  const syncHistory = useSyncProductHistory();
  const updateProduct = useUpdateProduct();
  const deleteProduct = useDeleteProduct();

  // 头部三动作（#856）：编辑开 ProductFormDialog；停用/启用与删除各经 ConfirmDialog 二次确认
  const [editOpen, setEditOpen] = useState(false);
  const [toggleOpen, setToggleOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // 同步周期（#856）：默认成立以来；自定义只选开始日期，结束恒为今天
  const [period, setPeriod] = useState<Period>("all");
  const [customStart, setCustomStart] = useState<Date | undefined>(undefined);

  // 分类维度字典：把产品五维 code 映射为中文名（产品行只存 code）
  const { data: dictData } = useAssetClassifications();
  const dimNameByCode = useMemo(
    () => new Map((dictData?.items ?? []).map((i) => [i.code, i.name])),
    [dictData?.items]
  );
  const dimLabel = (dimCode?: string | null) =>
    dimCode ? (dimNameByCode.get(dimCode) ?? dimCode) : "--";

  if (isLoading) {
    return <LoadingState />;
  }
  // 参数问题优先于数据问题：market 缺失时不拿后端的任何响应做归因
  if (!market) {
    return (
      <EmptyState
        message="缺少 market 参数"
        description="请从产品管理列表点击产品名称进入详情页"
      />
    );
  }
  // 后端对未知产品抛裸 HTTPException 404（无业务 code），react-query 视其为 error：
  // 须把 404 单独识别成「真·空态」，其余失败（5xx/网络）才走失败态（§1.2 口径①，
  // 失败不得伪装成空态、空态也不得伪装成失败）
  const notFound = isError && error instanceof ApiException && error.status === 404;
  if (isError && !notFound) {
    return <QueryErrorState error={error} onRetry={refetch} />;
  }
  if (notFound || !product) {
    return <EmptyState message="未找到该产品" description="产品可能已被删除" />;
  }

  const isNavType = !VIRTUAL_PRODUCT_TYPES.includes(product.product_type);
  const codeLabel = formatProductCodeLabel(product.display_code, product.code, product.market);

  // 周期同步（#856）：成立以来不发 body；其余档 start=今天自然回推、end=今天显式传。
  // 自定义未选开始日期时按钮禁用（syncDisabled），故 start 必有值。
  const today = new Date();
  const todayStr = toDateOnly(today);
  const syncDisabled = syncHistory.isPending || (period === "custom" && !customStart);
  const handleSyncHistory = () => {
    if (period === "all") {
      syncHistory.mutate({ code: product.code, market: product.market });
      return;
    }
    const start =
      period === "custom"
        ? customStart
        : period === "1m"
          ? subMonths(today, 1)
          : period === "3m"
            ? subMonths(today, 3)
            : period === "6m"
              ? subMonths(today, 6)
              : period === "1y"
                ? subYears(today, 1)
                : subYears(today, 3);
    if (!start) return;
    syncHistory.mutate({
      code: product.code,
      market: product.market,
      start_date: toDateOnly(start),
      end_date: todayStr,
    });
  };

  const dataSourceStatus =
    product.data_source_status === "success" ? (
      <span className="inline-flex items-center gap-1 text-success">
        <CheckCircle className="h-4 w-4" />
        正常
      </span>
    ) : product.data_source_status === "failed" ? (
      <span className="inline-flex items-center gap-1 text-destructive">
        <XCircle className="h-4 w-4" />
        失败
      </span>
    ) : isNavType ? (
      <span className="text-warning">待验证</span>
    ) : (
      <span className="text-muted-foreground">不适用</span>
    );

  const basicInfoCard = (
    <Card data-testid="product-basic-info-card">
      <CardHeader>
        <CardTitle className="text-lg">基本信息</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`grid gap-3 ${isMobile ? "grid-cols-2" : "grid-cols-3"}`}>
          <Field label="显示码" value={codeLabel} />
          <Field label="市场" value={formatMarketName(product.market)} />
          <Field label="类型" value={product.product_type} />
          <Field label="确认天数" value={`${product.confirm_days} 天`} />
          <Field
            label="估值滞后"
            value={(product.nav_lag_days ?? 0) > 0 ? `T-${product.nav_lag_days}` : "T"}
          />
          <Field label="QDII" value={product.is_qdii ? "是" : "否"} />
          <Field label="数据源" value={product.data_source || "--"} />
          <Field label="数据源状态" value={dataSourceStatus} />
          <Field
            label="最后同步"
            value={product.last_sync_at ? formatDateTime(product.last_sync_at) : "--"}
          />
          <Field
            label="创建时间"
            value={product.created_at ? formatDateTime(product.created_at) : "--"}
          />
        </div>
      </CardContent>
    </Card>
  );

  const dimensionCard = (
    <Card data-testid="product-dimension-card">
      <CardHeader>
        <CardTitle className="text-lg">分类维度</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`grid gap-3 ${isMobile ? "grid-cols-2" : "grid-cols-3"}`}>
          <Field label="大类" value={dimLabel(product.asset_class_code)} />
          <Field label="地区" value={dimLabel(product.region_code)} />
          <Field label="风格" value={dimLabel(product.style_code)} />
          <Field label="规模" value={dimLabel(product.size_code)} />
          <Field label="细分" value={dimLabel(product.segment_code)} />
        </div>
      </CardContent>
    </Card>
  );

  const syncCard = isNavType && (
    <Card data-testid="product-sync-card">
      <CardHeader>
        <CardTitle className="text-lg">数据同步</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Select value={period} onValueChange={(v) => setPeriod(v as Period)}>
              <SelectTrigger
                className="w-full sm:w-[150px]"
                data-testid="sync-period-select"
                aria-label="同步周期"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PERIOD_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {period === "custom" && (
              // #856：仅禁未来日期；不传 showTradingDays——否则 DatePicker 进入硬禁用模式
              // 会连带误禁非交易日，而周期窗口边界下非交易日是合法开始日。
              <DatePicker
                id="sync-start-date"
                date={customStart}
                onSelect={setCustomStart}
                placeholder="开始日期"
                className="w-full sm:w-[170px]"
                dayDisabled={(day) => toDateOnly(day) > todayStr}
              />
            )}
            <Button
              variant="outline"
              className="w-full sm:ml-auto sm:w-auto"
              onClick={handleSyncHistory}
              disabled={syncDisabled}
              data-testid="sync-history-button"
            >
              {syncHistory.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              同步历史
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {period === "all"
              ? "同步产品成立以来的全部历史净值。"
              : period === "custom"
                ? "自定义开始日期，结束日期为今天。"
                : "按所选周期重刷净值，结束日期为今天。"}
          </p>
        </div>
      </CardContent>
    </Card>
  );

  return (
    <div className={isMobile ? "space-y-3 p-3" : "space-y-4 p-6"}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Button asChild variant="ghost" size="icon" aria-label="返回" className="shrink-0">
            <Link href={`${basePath}/products`}>
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold">{product.name || codeLabel}</h1>
              {!product.is_active && (
                <Badge variant="neutral" className="shrink-0" data-testid="product-inactive-badge">
                  已停用
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground">
              {codeLabel} · {formatMarketName(product.market)}
            </p>
          </div>
        </div>
        {/* #856 头部三动作：移动端图标-only（aria-label 表意），桌面带文字 */}
        <div className="flex shrink-0 items-center gap-1 sm:gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setEditOpen(true)}
            aria-label="编辑"
            title="编辑"
          >
            <Pencil className="h-4 w-4 sm:mr-1.5" />
            <span className="hidden sm:inline">编辑</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setToggleOpen(true)}
            disabled={updateProduct.isPending}
            aria-label={product.is_active ? "停用" : "启用"}
            title={product.is_active ? "停用" : "启用"}
          >
            {product.is_active ? (
              <Ban className="h-4 w-4 sm:mr-1.5" />
            ) : (
              <CheckCircle className="h-4 w-4 sm:mr-1.5" />
            )}
            <span className="hidden sm:inline">{product.is_active ? "停用" : "启用"}</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setDeleteOpen(true)}
            disabled={deleteProduct.isPending}
            aria-label="删除"
            title="删除"
          >
            <Trash2 className="h-4 w-4 sm:mr-1.5" />
            <span className="hidden sm:inline">删除</span>
          </Button>
        </div>
      </div>

      {isMobile ? (
        <>
          {basicInfoCard}
          {dimensionCard}
          {syncCard}
          {isNavType && <ProductNavCards productCode={code} market={market} variant={variant} />}
        </>
      ) : (
        <div className="grid grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            {isNavType && <ProductNavCards productCode={code} market={market} variant={variant} />}
          </div>
          <div className="space-y-4">
            {basicInfoCard}
            {dimensionCard}
            {syncCard}
          </div>
        </div>
      )}

      {/* 仅打开时挂载：ProductFormDialog 也订阅维度字典，常驻会与维度卡订阅者叠加，
          让字典 500 时 per-subscriber 的 #214 toast 弹两条（#856）。 */}
      {editOpen && (
        <ProductFormDialog open={editOpen} onOpenChange={setEditOpen} editingProduct={product} />
      )}

      <ConfirmDialog
        open={toggleOpen}
        onOpenChange={setToggleOpen}
        title={product.is_active ? "停用产品" : "启用产品"}
        description={
          product.is_active
            ? DEACTIVATE_HINT
            : "启用后可恢复新建该产品的买入/卖出记录。"
        }
        confirmText={product.is_active ? "停用" : "启用"}
        onConfirm={() => {
          setToggleOpen(false);
          updateProduct.mutate({
            code: product.code,
            market: product.market,
            data: { is_active: !product.is_active },
          });
        }}
      />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="删除产品"
        description="确定要删除该产品吗？已被持仓或交易引用的产品无法删除。"
        confirmText="删除"
        onConfirm={() => {
          setDeleteOpen(false);
          deleteProduct.mutate(
            { code: product.code, market: product.market },
            { onSuccess: () => router.push(`${basePath}/products`) }
          );
        }}
      />
    </div>
  );
}
