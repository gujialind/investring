"use client";

import { useEffect } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import type { AssetClassificationItem } from "@/types/asset-classification";
import type {
  HoldingPlatformAggregate,
  HoldingsView,
  HoldingProductAggregate,
} from "@/types/holding";
import {
  useHoldingsByPlatform,
  useHoldingsByProduct,
} from "@/hooks/usePosition";
import { assetClassColor, otherColor } from "@/lib/colors";
import { useChartTheme } from "@/hooks/useChartTheme";
import { CASH_PRODUCT_CODE, PSEUDO_IN_TRANSIT_CODE } from "@/lib/allocation";
import {
  buildCardPercentMap,
  buildProductSections,
  CUMULATIVE_PROFIT_INFO_TIP,
  formatHoldingProfit,
  holdingSnapshotMmdd,
  MOBILE_PRODUCT_RETURN_PREFIX,
  productCardKey,
  productDetailHref,
  sumGroupPercent,
  type HoldingProductSection,
} from "@/lib/holdings";
import {
  DIMENSION_LABELS,
  isOverrideRegistered,
  registeredDimsFor,
  ruleDimsFor,
  type DimensionRules,
  type SubDimension,
  type SubDimOverrides,
} from "@/lib/dimensions";
import {
  cn,
  formatCurrency,
  formatNumber,
  formatSignedCurrency,
  getReturnColorClass,
  largestRemainderPercents,
} from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import HoldingInTransitCard from "./HoldingInTransitCard";
import HoldingProductCard from "./HoldingProductCard";
import HoldingPlatformCard from "./HoldingPlatformCard";
import InfoTip from "./InfoTip";
import LoadingState from "./LoadingState";
import ProductCell from "./ProductCell";
import QueryErrorState from "./QueryErrorState";

interface PortfolioHoldingsProps {
  portfolioCode: string;
  /** 链接前缀：桌面 "/portfolio"，移动 "/m/portfolio"（产品卡 → 产品详情页，#595 步骤③） */
  basePath: string;
  /** asset_class 维度字典（分区顺序/颜色驱动，issue #128；二级组合键子集见 displayConfig/dimensionRules） */
  assetClasses: AssetClassificationItem[];
  /** 组合级二级组合键维度子集覆盖（issue #144/#915，portfolio.display_config 契约原样传入） */
  displayConfig?: Record<string, string> | null;
  /** 维度级适用矩阵（#915：读侧子集解析按登记防御回退；页面自字典响应下传，零额外请求） */
  dimensionRules?: DimensionRules | null;
  /** 当前视图（D-7：URL ?view=product|platform 由页面持有，本组件不读路由） */
  view: HoldingsView;
  onViewChange: (view: HoldingsView) => void;
  /** 页面级临时子集覆盖（#915 B2：URL ?sub= 由页面持有，本组件不读路由）；
   * 与 onSubDimToggle 同时提供时才渲染分区头下的切换 chip 行 */
  subDimOverrides?: SubDimOverrides | null;
  /** 切换意图回调（dim=null = 「默认」项）；合并基与 URL 写入由页面同步持有（ocr r2 竞态） */
  onSubDimToggle?: (classCode: string, dim: SubDimension | null) => void;
  /** 标题右侧操作位（如「分组维度」按钮） */
  action?: ReactNode;
  variant: "desktop" | "mobile";
}

const VIEW_TABS: { key: HoldingsView; label: string }[] = [
  { key: "product", label: "按产品" },
  { key: "platform", label: "按平台" },
];

/**
 * 卡片栅格：桌面用于在途卡与 PlatformDetailContent 持仓明细；移动产品列表传
 * "product-rows" 分隔线行，其余平台卡/在途卡沿用 "cards" 间距。
 * PortfolioHoldings 桌面产品/平台视图已改为通栏账表，不再消费该栅格。
 */
export function cardGridClass(
  variant: "desktop" | "mobile",
  layout: "cards" | "product-rows" = "cards",
): string {
  if (variant === "desktop") {
    return "grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(350px,100%),1fr))]";
  }
  return layout === "product-rows" ? "divide-y divide-border" : "space-y-2.5";
}

/** 分区头下的子集切换 chip 行（#915 B2：快捷切换单维 + 页面直切子集，同一交互）。
 * 「默认」= 跟随组合配置/默认子集（清掉该大类 URL 覆盖）；维度 chip 默认态首击
 * 切换到该单维、覆盖态点击为勾选/取消（toggleSubDimOverride 单点持有语义）。
 * 纯视图状态：仅经 onSubDimToggle（行内 onToggle）回写 URL，不发任何写请求。
 * 双端同形态（信息列双端一致，§6）：flex-wrap 换行，chip 触控档同净值区间先例。 */
function SubDimSwitchRow({
  sectionCode,
  sectionName,
  registered,
  ruleDims,
  overrides,
  onToggle,
}: {
  sectionCode: string;
  sectionName: string;
  /** 该大类登记维（own 键，lib 单点判据产出）；切换行仅在其非空时渲染 */
  registered: string[];
  /**  chip 枚举（ruleDimsFor 单点产出，门控与渲染同源） */
  ruleDims: SubDimension[];
  overrides: SubDimOverrides;
  /** dim=null 表示「默认」项（清除该大类覆盖）；合并基由页面同步持有（ocr r2 竞态） */
  onToggle: (classCode: string, dim: SubDimension | null) => void;
}) {
  const override = overrides[sectionCode];
  // 有效性判据与消费侧 resolveViewSubDims 结构同源（lib 单点，ocr r3）：
  // 继承成员/非数组/含未登记维（规则事后收紧/手工 URL）→ 按默认态渲染，
  // 且首击重建子集（lib toggle 同判据），避免「点任何 chip 分组恒不动」死胡同（ocr r1）
  const overrideValid = isOverrideRegistered(override, registered);
  const active = overrideValid ? override : null;
  const overrideActive = active !== null;
  const chipClass = (pressed: boolean) =>
    `shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 text-xs transition-colors ${
      pressed
        ? "bg-primary font-semibold text-primary-foreground"
        : "bg-muted text-muted-foreground hover:bg-accent"
    }`;
  return (
    <div
      role="group"
      aria-label={`${sectionName}分组维度切换`}
      data-testid={`sub-dim-switch-${sectionCode}`}
      className="flex flex-wrap gap-1.5 px-1 pb-2"
    >
      <button
        type="button"
        data-testid={`sub-dim-chip-${sectionCode}-default`}
        aria-pressed={!overrideActive}
        title="跟随组合配置/默认子集分组"
        onClick={() => onToggle(sectionCode, null)}
        className={chipClass(!overrideActive)}
      >
        默认
      </button>
      {ruleDims.map((d) => (
        <button
          key={d}
          type="button"
          data-testid={`sub-dim-chip-${sectionCode}-${d}`}
          aria-pressed={active?.includes(d) ?? false}
          onClick={() => onToggle(sectionCode, d)}
          className={chipClass(active?.includes(d) ?? false)}
        >
          {DIMENSION_LABELS[d] ?? d}
        </button>
      ))}
    </div>
  );
}

function DesktopProductTable({
  products,
  percentOf,
  snapshotDate,
  productLinkPrefix,
}: {
  products: HoldingProductAggregate[];
  percentOf: (product: HoldingProductAggregate) => number;
  snapshotDate?: string | null;
  productLinkPrefix: string;
}) {
  const mmdd = holdingSnapshotMmdd(snapshotDate);
  return (
    <Table
      data-testid="holding-product-table"
      containerClassName="lg:max-h-none"
    >
      <TableHeader className="lg:static lg:bg-background">
        <TableRow>
          <TableHead>产品</TableHead>
          <TableHead className="number-cell">市值</TableHead>
          <TableHead className="number-cell">占比</TableHead>
          <TableHead className="number-cell">
            <span className="inline-flex items-center">
              累计收益
              <InfoTip {...CUMULATIVE_PROFIT_INFO_TIP} />
            </span>
          </TableHead>
          <TableHead className="number-cell">
            最新收益{mmdd ? `(${mmdd})` : ""}
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {products.map((product) => {
          const isCash = product.product_code === CASH_PRODUCT_CODE;
          const href = productDetailHref(productLinkPrefix, product);
          return (
            <TableRow key={productCardKey(product)} data-testid="holding-product-row">
              <TableCell>
                <Link
                  href={href}
                  aria-label={`查看${isCash ? "可用现金" : product.product_name || product.product_code}详情`}
                  data-product-code={product.product_code}
                  data-product-key={productCardKey(product)}
                  className="rounded-sm font-medium text-foreground outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {isCash ? (
                    "可用现金"
                  ) : (
                    <ProductCell
                      code={product.product_code}
                      name={product.product_name ?? undefined}
                      market={product.market}
                      displayCode={product.display_code}
                    />
                  )}
                </Link>
              </TableCell>
              <TableCell className="number-cell whitespace-nowrap">
                {formatCurrency(product.market_value)}
              </TableCell>
              <TableCell
                data-testid="holding-ratio-value"
                className="number-cell whitespace-nowrap"
              >
                {percentOf(product).toFixed(1)}%
              </TableCell>
              <TableCell
                data-testid="total-profit-value"
                className={cn(
                  "number-cell whitespace-nowrap",
                  !isCash && getReturnColorClass(product.cumulative_profit),
                )}
              >
                {isCash ? null : formatHoldingProfit(product.cumulative_profit)}
              </TableCell>
              <TableCell
                data-testid="daily-profit-value"
                className={cn(
                  "number-cell whitespace-nowrap",
                  !isCash && getReturnColorClass(product.daily_profit),
                )}
              >
                {isCash ? null : formatHoldingProfit(product.daily_profit)}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function DesktopPlatformTable({
  platforms,
  percents,
  portfolioCode,
  basePath,
}: {
  platforms: HoldingPlatformAggregate[];
  percents: number[];
  portfolioCode: string;
  basePath: string;
}) {
  return (
    <Table
      data-testid="holding-platform-table"
      containerClassName="lg:max-h-none"
    >
      <TableHeader className="lg:static lg:bg-background">
        <TableRow>
          <TableHead>平台</TableHead>
          <TableHead className="number-cell">市值</TableHead>
          <TableHead className="number-cell">占比</TableHead>
          <TableHead className="number-cell">持有收益</TableHead>
          <TableHead>产品与现金</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {platforms.map((platform, index) => {
          const name = platform.platform_name || platform.platform_code || "--";
          const identity = (
            <>
              <span className="block text-sm font-medium">{name}</span>
              {platform.platform_code && (
                <span className="block whitespace-nowrap text-xs text-muted-foreground">
                  {platform.platform_code}
                </span>
              )}
            </>
          );
          return (
            <TableRow key={platform.platform_code ?? "anonymous-platform"} data-testid="holding-platform-row">
              <TableCell>
                {platform.platform_code ? (
                  <Link
                    href={`${basePath}/${portfolioCode}/platforms/${platform.platform_code}`}
                    aria-label={`查看${name}详情`}
                    className="rounded-sm outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {identity}
                  </Link>
                ) : (
                  identity
                )}
              </TableCell>
              <TableCell className="number-cell whitespace-nowrap">
                {formatCurrency(platform.market_value)}
              </TableCell>
              <TableCell className="number-cell whitespace-nowrap">
                {(percents[index] ?? 0).toFixed(1)}%
              </TableCell>
              <TableCell
                className={cn(
                  "number-cell whitespace-nowrap",
                  getReturnColorClass(platform.holding_profit),
                )}
              >
                {formatSignedCurrency(platform.holding_profit)}
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                {platform.product_count} 只产品
                {platform.cash_balance !== 0 &&
                  ` · 现金 ${formatCurrency(platform.cash_balance, 0)}`}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/** 按产品分区渲染：桌面通栏账表，移动分隔线行；#915 分区与子集切换语义不变。 */
function ProductSections({
  portfolioCode,
  sections,
  percentOf,
  snapshotDate,
  variant,
  productLinkPrefix,
  dimensionRules,
  subDimOverrides,
  onSubDimToggle,
}: {
  portfolioCode: string;
  sections: HoldingProductSection[];
  /** 行级占比查表（buildCardPercentMap 输出），§4 分区头 = 行占比加总后取整 */
  percentOf: (product: HoldingProductAggregate) => number;
  snapshotDate?: string | null;
  variant: "desktop" | "mobile";
  /** 产品卡跳转前缀：`${basePath}/${portfolioCode}/product`（market 走 ?market=，现金卡步骤⑤已接线） */
  productLinkPrefix: string;
  /** 维度级适用矩阵（B2 切换 chip 行枚举非禁止维；缺失时不渲染切换行） */
  dimensionRules?: DimensionRules | null;
  subDimOverrides?: SubDimOverrides | null;
  onSubDimToggle?: (classCode: string, dim: SubDimension | null) => void;
}) {
  const theme = useChartTheme();
  return (
    <>
      {sections.map((section) => {
        const color =
          section.sortOrder !== null
            ? assetClassColor(section.sortOrder, theme)
            : otherColor(theme);
        const sectionPercent = section.groups.reduce(
          (s, g) => s + sumGroupPercent(g.products, percentOf),
          0
        );
        // B2 切换行：仅字典大类且规则矩阵有行者（现金 0 维/「其他」桶不出现）。
        // 枚举与有效性判据走 lib 单点（ocr r2/r3：与 resolver 同口径 own 键）
        const sectionCode = section.code;
        const registered = sectionCode
          ? registeredDimsFor(sectionCode, dimensionRules)
          : null;
        const ruleDims = registered ? ruleDimsFor(registered) : [];
        const showSwitch =
          !!sectionCode &&
          ruleDims.length > 0 &&
          !!subDimOverrides &&
          !!onSubDimToggle;
        return (
          <div key={section.code ?? "__other__"}>
            {/* 大类分区头：色点 + 大类名 + 合计 + 取整占比 */}
            <div className="flex items-baseline justify-between px-1 pb-2 pt-4">
              <span className="flex items-center text-sm font-bold">
                <span
                  className="mr-1.5 h-2 w-2 rounded-sm"
                  style={{ background: color }}
                />
                {section.name}
              </span>
              <span className="text-sm font-bold number-cell">
                {formatNumber(section.total)}
                <span className="ml-0.5 text-xs font-normal text-muted-foreground">元</span>
                <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                  {Math.round(sectionPercent)}%
                </span>
              </span>
            </div>
            {showSwitch && registered && (
              <SubDimSwitchRow
                sectionCode={sectionCode}
                sectionName={section.name}
                registered={registered}
                ruleDims={ruleDims}
                overrides={subDimOverrides}
                onToggle={onSubDimToggle}
              />
            )}
            <div className="space-y-3">
              {section.groups.map((g) => {
                // V4 契约：chip 恒在产品卡之上，仅组名与大类同名（平铺组）时不渲染
                const showChip = g.name !== section.name;
                return (
                  <div key={g.codeKey} data-testid="asset-group">
                    {showChip && (
                      <div
                        data-testid="asset-group-header"
                        className="mb-2 flex items-center justify-between px-0.5"
                      >
                        <span className="inline-flex items-center rounded bg-muted px-2 py-0.5 text-xs font-semibold text-foreground-secondary">
                          <span
                            className="mr-1.5 h-1.5 w-1.5 rounded-full"
                            style={{ background: color }}
                          />
                          {g.name}
                        </span>
                        <span className="text-sm font-bold number-cell">
                          {formatNumber(g.total)}
                          <span className="ml-0.5 text-xs font-normal text-muted-foreground">
                            元
                          </span>
                          <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                            {Math.round(sumGroupPercent(g.products, percentOf))}%
                          </span>
                        </span>
                      </div>
                    )}
                    <div
                      data-testid="asset-group-cards"
                      className={variant === "desktop" ? "w-full" : cardGridClass("mobile", "product-rows")}
                    >
                      {variant === "desktop" ? (
                        <DesktopProductTable
                          products={g.products}
                          percentOf={percentOf}
                          snapshotDate={snapshotDate}
                          productLinkPrefix={productLinkPrefix}
                        />
                      ) : (
                        g.products.map((p) => (
                          <HoldingProductCard
                            key={productCardKey(p)}
                            product={p}
                            percent={percentOf(p)}
                            snapshotDate={snapshotDate}
                            variant="mobile"
                            returnFocusKey={portfolioCode}
                            href={productDetailHref(productLinkPrefix, p)}
                          />
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </>
  );
}

/**
 * #595 组合详情页持仓明细区：「按产品 / 按平台」分段切换 + 双视图。
 * 视图状态由页面经 URL ?view= 持久化；两个聚合查询按当前视图惰性启用。
 * 行级占比一律最大余数法（§4）：产品视图行集=产品卡（含现金）+在途聚合卡，
 * 平台视图行集=平台卡；分区头 = 行占比加总后取整，与饼图同一分配体系。
 * 空态与 spec §6 过滤由后端保证，这里只兜「全过滤后为空」的文案。
 */
export default function PortfolioHoldings({
  portfolioCode,
  basePath,
  assetClasses,
  displayConfig,
  dimensionRules,
  view,
  onViewChange,
  subDimOverrides,
  onSubDimToggle,
  action,
  variant,
}: PortfolioHoldingsProps) {
  const productQuery = useHoldingsByProduct(portfolioCode, view === "product");
  const platformQuery = useHoldingsByPlatform(portfolioCode, view === "platform");
  const activeQuery = view === "product" ? productQuery : platformQuery;

  const productData = productQuery.data;
  const inTransitValue = productData?.in_transit_market_value ?? 0;
  /** 产品视图行级占比查表：产品卡（含现金）+ 在途卡（>0 时）同一行集分配 */
  const productPercentMap = productData
    ? buildCardPercentMap(productData.products, inTransitValue)
    : null;
  const percentOfProduct = (
    p: NonNullable<typeof productData>["products"][number]
  ) => productPercentMap?.get(productCardKey(p)) ?? 0;

  // 移动端从产品详情返回：聚焦点击前那一行（键按组合隔离，焦点归还后即清除）
  useEffect(() => {
    if (variant !== "mobile" || view !== "product" || !productData) return;
    const storageKey = `${MOBILE_PRODUCT_RETURN_PREFIX}${portfolioCode}`;
    let productKey: string | null = null;
    try {
      productKey = sessionStorage.getItem(storageKey);
    } catch {
      return;
    }
    if (!productKey) return;
    const frame = requestAnimationFrame(() => {
      const row = document.querySelector<HTMLAnchorElement>(
        `[data-testid="portfolio-holdings"] [data-product-key="${CSS.escape(productKey)}"]`,
      );
      row?.focus({ preventScroll: true });
      row?.scrollIntoView({ block: "center" });
      try {
        sessionStorage.removeItem(storageKey);
      } catch {
        // 焦点已恢复，清理失败不影响本次返回。
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [variant, view, productData, portfolioCode]);

  const sections =
    view === "product" && productData
      ? buildProductSections(productData.products, assetClasses, displayConfig, dimensionRules, subDimOverrides)
      : [];
  const platforms = platformQuery.data?.platforms ?? [];
  /** 平台视图行级占比：平台市值在视图内最大余数法分配（§4） */
  const platformPercents = largestRemainderPercents(
    platforms.map((p) => p.market_value)
  );

  const productEmpty = sections.length === 0 && inTransitValue <= 0;

  return (
    <div className="space-y-1" data-testid="portfolio-holdings">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold leading-6">持仓明细</h3>
        {action}
      </div>

      {/* 视图分段切换（D-7）：持久化在 URL，由页面 onViewChange 回写 */}
      <div
        className="mt-2 grid grid-cols-2 gap-1 rounded-lg bg-muted p-1"
        data-testid="holdings-view-tabs"
      >
        {VIEW_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            data-testid={`holdings-tab-${tab.key}`}
            aria-pressed={view === tab.key}
            onClick={() => onViewChange(tab.key)}
            className={`rounded-md py-1.5 text-sm transition-colors ${
              view === tab.key
                ? "bg-card font-semibold text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeQuery.isLoading ? (
        <LoadingState />
      ) : activeQuery.isError ? (
        <QueryErrorState
          error={activeQuery.error}
          onRetry={() => void activeQuery.refetch()}
        />
      ) : view === "product" ? (
        productEmpty ? (
          <div className="py-8 text-center text-muted-foreground">暂无持仓记录</div>
        ) : (
          <>
            <ProductSections
              portfolioCode={portfolioCode}
              sections={sections}
              percentOf={percentOfProduct}
              snapshotDate={productData?.snapshot_date}
              variant={variant}
              productLinkPrefix={`${basePath}/${portfolioCode}/product`}
              dimensionRules={dimensionRules}
              subDimOverrides={subDimOverrides}
              onSubDimToggle={onSubDimToggle}
            />
            {inTransitValue > 0 && (
              <div className={`mt-3 ${cardGridClass(variant)}`}>
                <HoldingInTransitCard
                  marketValue={inTransitValue}
                  percent={
                    productPercentMap?.get(PSEUDO_IN_TRANSIT_CODE) ?? 0
                  }
                />
              </div>
            )}
          </>
        )
      ) : platforms.length === 0 ? (
        <div className="py-8 text-center text-muted-foreground">暂无持仓记录</div>
      ) : variant === "desktop" ? (
        <div className="pt-3" data-testid="platform-group">
          <div
            data-testid="platform-group-header"
            className="mb-2 flex items-baseline justify-between px-1"
          >
            <span className="text-sm font-bold">平台合计</span>
            <span className="number-cell text-sm font-bold">
              {formatCurrency(platforms.reduce((sum, platform) => sum + platform.market_value, 0))}
            </span>
          </div>
          <DesktopPlatformTable
            platforms={platforms}
            percents={platformPercents}
            portfolioCode={portfolioCode}
            basePath={basePath}
          />
        </div>
      ) : (
        <div className={`pt-3 ${cardGridClass("mobile")}`}>
          {platforms.map((p, i) => {
            const card = (
              <HoldingPlatformCard
                platform={p}
                percent={platformPercents[i] ?? 0}
              />
            );
            // S10：匿名平台（platform_code 为 NULL）不包 Link，避免残缺路由死链
            if (!p.platform_code) {
              return <div key="anonymous-platform">{card}</div>;
            }
            return (
              <Link
                key={p.platform_code}
                href={`${basePath}/${portfolioCode}/platforms/${p.platform_code}`}
                className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {card}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
