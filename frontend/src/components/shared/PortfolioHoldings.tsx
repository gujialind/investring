"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import type { AssetClassificationItem } from "@/types/asset-classification";
import type { HoldingsView, HoldingProductAggregate } from "@/types/holding";
import {
  useHoldingsByPlatform,
  useHoldingsByProduct,
} from "@/hooks/usePosition";
import { assetClassColor, otherColor } from "@/lib/colors";
import { useChartTheme } from "@/hooks/useChartTheme";
import { PSEUDO_IN_TRANSIT_CODE } from "@/lib/allocation";
import {
  buildCardPercentMap,
  buildProductSections,
  productCardKey,
  sumGroupPercent,
  type HoldingProductSection,
} from "@/lib/holdings";
import {
  DIMENSION_LABELS,
  RULE_DIMENSIONS,
  isOverrideRegistered,
  registeredDimsFor,
  type SubDimension,
  type SubDimOverrides,
} from "@/lib/dimensions";
import { cn, formatNumber, largestRemainderPercents } from "@/lib/utils";
import HoldingInTransitCard from "./HoldingInTransitCard";
import HoldingProductCard from "./HoldingProductCard";
import HoldingPlatformCard from "./HoldingPlatformCard";
import LoadingState from "./LoadingState";
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
  dimensionRules?: Record<string, Record<string, string>> | null;
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
 * 产品卡栅格：移动 1 列、桌面 auto-fill minmax（spec「桌面 2 列」，单卡宽
 * 设计区间 ~358-372、允许栅格微调）。min 取 350 而非 360：桌面带引导线的维度组
 * 内容宽比平铺组少 16px（ml-0.5(2) + border-l-2(2) + pl-3(12)；#724 起引导线仅桌面保留，
 * 移动端两组等宽、不受此约束），auto-fill 按 content-box
 * 算列数，min=360 会在 1440 档使两组列数分叉（隔离栈探针实测引导组 720px→1 列 vs
 * 平铺组 736px→2 列，同页卡宽不一致）；min ≤ (720-12)/2 = 354 时两组同列数，
 * 单卡实宽 354（引导组）-362（平铺组）——引导组下界比设计区间少 4px，是换两组同列数的代价。
 *
 * **消费点不止本文件**（#750 收敛后该串仅此一处持有）：按产品视图的引导组 / 平铺组与
 * 在途卡、按平台视图的平台卡组，以及 `PlatformDetailContent.tsx` 的平台详情「持仓明细」。
 * 该页没有引导线结构、不参与上面的分叉推导，但列数与卡宽同样由这个 350 决定——按引导线
 * 理由调 350 会连带改动它，两处需一起目检。
 */
export function cardGridClass(variant: "desktop" | "mobile"): string {
  return variant === "desktop"
    ? "grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(350px,1fr))]"
    : "space-y-2.5";
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
  overrides,
  onToggle,
}: {
  sectionCode: string;
  sectionName: string;
  /** 该大类登记维（own 键，lib 单点判据产出）；切换行仅在其非空时渲染 */
  registered: string[];
  overrides: SubDimOverrides;
  /** dim=null 表示「默认」项（清除该大类覆盖）；合并基由页面同步持有（ocr r2 竞态） */
  onToggle: (classCode: string, dim: SubDimension | null) => void;
}) {
  const ruleDims = RULE_DIMENSIONS.filter((d) => registered.includes(d));
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

/** 按产品分区渲染（V4 语义：分区头 → chip → 卡组；引导线缩进仅桌面，#724）。
 * #915：二级组为维度子集组合键，React key 用 code 元组串（同名不同 code 不冲突）；
 * showChip 判据（组名 ≠ 大类名）原样保留。 */
function ProductSections({
  sections,
  percentOf,
  snapshotDate,
  variant,
  productLinkPrefix,
  dimensionRules,
  subDimOverrides,
  onSubDimToggle,
}: {
  sections: HoldingProductSection[];
  /** 行级占比查表（buildCardPercentMap 输出），§4 分区头 = 行占比加总后取整 */
  percentOf: (product: HoldingProductAggregate) => number;
  snapshotDate?: string | null;
  variant: "desktop" | "mobile";
  /** 产品卡跳转前缀：`${basePath}/${portfolioCode}/product`（market 走 ?market=，现金卡步骤⑤已接线） */
  productLinkPrefix: string;
  /** 维度级适用矩阵（B2 切换 chip 行枚举非禁止维；缺失时不渲染切换行） */
  dimensionRules?: Record<string, Record<string, string>> | null;
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
        const ruleDims = registered
          ? RULE_DIMENSIONS.filter((d) => registered.includes(d))
          : [];
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
            {showSwitch && sectionCode && onSubDimToggle && registered && (
              <SubDimSwitchRow
                sectionCode={sectionCode}
                sectionName={section.name}
                registered={registered}
                overrides={subDimOverrides ?? {}}
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
                    {/* 引导线缩进仅桌面保留（§6 第 4 类；量级与理由由该条持有）。
                        testid 是 E2E 定位缩进载体的锚点——#382 口径：需锚点补 testid，不按 DOM 深度跳 */}
                    <div
                      data-testid="asset-group-cards"
                      className={cn(
                        showChip && variant === "desktop" &&
                          "ml-0.5 border-l-2 border-border pl-3",
                        cardGridClass(variant)
                      )}
                    >
                      {g.products.map((p) => {
                        const card = (
                          <HoldingProductCard
                            product={p}
                            percent={percentOf(p)}
                            snapshotDate={snapshotDate}
                          />
                        );
                        // #595 步骤③/⑤：产品卡点击 → 产品详情页；
                        // market 走 ?market=（步骤⑤起），现金 market="" 与平台-产品页现金形态一致
                        return (
                          <Link
                            key={productCardKey(p)}
                            href={`${productLinkPrefix}/${encodeURIComponent(p.product_code)}?market=${encodeURIComponent(p.market)}`}
                            className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {card}
                          </Link>
                        );
                      })}
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
        <h3 className="text-lg font-semibold">持仓明细</h3>
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
      ) : (
        <div className={`pt-3 ${cardGridClass(variant)}`}>
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
