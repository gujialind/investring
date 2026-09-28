"use client";

import type { ReactNode } from "react";
import type { AssetClassificationItem } from "@/types/asset-classification";
import type { HoldingsView, HoldingProductAggregate } from "@/types/holding";
import {
  useHoldingsByPlatform,
  useHoldingsByProduct,
} from "@/hooks/usePosition";
import { assetClassColor, OTHER_COLOR } from "@/lib/colors";
import { PSEUDO_IN_TRANSIT_CODE } from "@/lib/allocation";
import {
  buildCardPercentMap,
  buildProductSections,
  productCardKey,
  sumGroupPercent,
  type HoldingProductSection,
} from "@/lib/holdings";
import { formatNumber, largestRemainderPercents } from "@/lib/utils";
import HoldingInTransitCard from "./HoldingInTransitCard";
import HoldingProductCard from "./HoldingProductCard";
import HoldingPlatformCard from "./HoldingPlatformCard";
import LoadingState from "./LoadingState";

interface PortfolioHoldingsProps {
  portfolioCode: string;
  /** asset_class 维度字典（分区顺序/颜色/二级分组维度驱动，issue #128） */
  assetClasses: AssetClassificationItem[];
  /** 组合级二级分组维度覆盖（issue #144，portfolio.display_config 契约原样传入） */
  displayConfig?: Record<string, string> | null;
  /** 当前视图（D-7：URL ?view=product|platform 由页面持有，本组件不读路由） */
  view: HoldingsView;
  onViewChange: (view: HoldingsView) => void;
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
 * 设计区间 ~358-372、允许栅格微调）。min 取 350 而非 360：带引导线的维度组
 * 内容宽比平铺组少 14px（border-l-2 + pl-3 + ml-0.5），auto-fill 按 content-box
 * 算列数，min=360 会在 1440 档使两组列数分叉（实测 722px→1 列 vs 736px→2 列，
 * 同页卡宽不一致）；min ≤ (722-12)/2 时两组同列数，单卡实宽 355-372 仍在区间内。
 */
function cardGridClass(variant: "desktop" | "mobile"): string {
  return variant === "desktop"
    ? "grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(350px,1fr))]"
    : "space-y-2.5";
}

/** 按产品分区渲染（与 PositionSections 同 V4 语义：分区头 → chip → 引导线卡组） */
function ProductSections({
  sections,
  percentOf,
  snapshotDate,
  variant,
}: {
  sections: HoldingProductSection[];
  /** 行级占比查表（buildCardPercentMap 输出），§4 分区头 = 行占比加总后取整 */
  percentOf: (product: HoldingProductAggregate) => number;
  snapshotDate?: string | null;
  variant: "desktop" | "mobile";
}) {
  return (
    <>
      {sections.map((section) => {
        const color =
          section.sortOrder !== null
            ? assetClassColor(section.sortOrder)
            : OTHER_COLOR;
        const sectionPercent = section.groups.reduce(
          (s, g) => s + sumGroupPercent(g.products, percentOf),
          0
        );
        return (
          <div key={section.name}>
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
            <div className="space-y-3">
              {section.groups.map((g) => {
                // V4 契约：chip 恒在产品卡之上，仅组名与大类同名（平铺组）时不渲染
                const showChip = g.name !== section.name;
                return (
                  <div key={g.name} data-testid="asset-group">
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
                      className={
                        showChip
                          ? `ml-0.5 border-l-2 border-border pl-3 ${cardGridClass(variant)}`
                          : cardGridClass(variant)
                      }
                    >
                      {g.products.map((p) => (
                        <HoldingProductCard
                          key={productCardKey(p)}
                          product={p}
                          percent={percentOf(p)}
                          snapshotDate={snapshotDate}
                        />
                      ))}
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
  assetClasses,
  displayConfig,
  view,
  onViewChange,
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
      ? buildProductSections(productData.products, assetClasses, displayConfig)
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
        <div className="py-8 text-center text-muted-foreground">
          加载失败，请刷新重试
        </div>
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
          {platforms.map((p, i) => (
            <HoldingPlatformCard
              key={p.platform_code ?? "unknown"}
              platform={p}
              percent={platformPercents[i] ?? 0}
            />
          ))}
        </div>
      )}
    </div>
  );
}
