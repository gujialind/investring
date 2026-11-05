"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, CheckCircle, XCircle, RefreshCw, TrendingUp, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import QueryErrorState from "./QueryErrorState";
import ProductNavCards from "./ProductNavCards";
import { useProduct, useSyncProductPrice, useSyncProductHistory } from "@/hooks/useProduct";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import { ApiException } from "@/lib/api";
import {
  formatDateTime,
  formatMarketName,
  formatProductCodeLabel,
} from "@/lib/utils";

// 虚拟产品判定（#514/#327）：对齐后端 product_service.VIRTUAL_PRODUCT_TYPES——无净值、不可交易，
// 故不渲染净值三卡与同步操作（与 ProductsContent 同一口径）。
const VIRTUAL_PRODUCT_TYPES = ["CASH", "IN_TRANSIT"];

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
  const searchParams = useSearchParams();
  const code = params.code as string;
  const market = searchParams.get("market") ?? "";
  const isMobile = variant === "mobile";

  const { data: product, isLoading, isError, error, refetch } = useProduct(code, market);
  const syncPrice = useSyncProductPrice();
  const syncHistory = useSyncProductHistory();

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
        <div className="flex gap-2">
          <Button
            variant="outline"
            className="flex-1"
            onClick={() => syncPrice.mutate({ code: product.code, market: product.market })}
            disabled={syncPrice.isPending || syncHistory.isPending}
          >
            {syncPrice.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            同步价格
          </Button>
          <Button
            variant="outline"
            className="flex-1"
            onClick={() => syncHistory.mutate({ code: product.code, market: product.market })}
            disabled={syncPrice.isPending || syncHistory.isPending}
          >
            {syncHistory.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <TrendingUp className="mr-2 h-4 w-4" />
            )}
            同步历史
          </Button>
        </div>
      </CardContent>
    </Card>
  );

  return (
    <div className={isMobile ? "space-y-3 p-3" : "space-y-4 p-6"}>
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="icon" aria-label="返回">
          <Link href={`${basePath}/products`}>
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-lg font-semibold">{product.name || codeLabel}</h1>
          <p className="text-sm text-muted-foreground">
            {codeLabel} · {formatMarketName(product.market)}
          </p>
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
    </div>
  );
}
