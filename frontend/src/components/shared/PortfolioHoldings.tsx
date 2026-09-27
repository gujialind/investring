"use client";

import type { ReactNode } from "react";
import type { AssetClassificationItem } from "@/types/asset-classification";
import type { HoldingsView } from "@/types/holding";
import {
  useHoldingsByPlatform,
  useHoldingsByProduct,
} from "@/hooks/usePosition";
import { assetClassColor, OTHER_COLOR } from "@/lib/colors";
import {
  buildProductSections,
  sumGroupPercent,
  type HoldingProductSection,
} from "@/lib/holdings";
import { formatNumber } from "@/lib/utils";
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

/** 产品卡栅格：移动 1 列、桌面 auto-fill minmax（spec §7，卡片宽 360+） */
function cardGridClass(variant: "desktop" | "mobile"): string {
  return variant === "desktop"
    ? "grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(360px,1fr))]"
    : "space-y-2.5";
}

/** 按产品分区渲染（与 PositionSections 同 V4 语义：分区头 → chip → 引导线卡组） */
function ProductSections({
  sections,
  snapshotDate,
  variant,
}: {
  sections: HoldingProductSection[];
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
          (s, g) => s + sumGroupPercent(g.products),
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
              <span className="text-sm font-bold tabular-nums">
                {formatNumber(section.total)}
                <span className="ml-0.5 text-xs font-normal text-slate-500">元</span>
                <span className="ml-1.5 text-xs font-normal text-slate-500">
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
                        <span className="text-sm font-bold tabular-nums">
                          {formatNumber(g.total)}
                          <span className="ml-0.5 text-xs font-normal text-slate-500">
                            元
                          </span>
                          <span className="ml-1.5 text-xs font-normal text-slate-500">
                            {Math.round(sumGroupPercent(g.products))}%
                          </span>
                        </span>
                      </div>
                    )}
                    <div
                      className={
                        showChip
                          ? `ml-0.5 border-l-2 border-slate-100 pl-3 ${cardGridClass(variant)}`
                          : cardGridClass(variant)
                      }
                    >
                      {g.products.map((p) => (
                        <HoldingProductCard
                          key={`${p.product_code}-${p.market}`}
                          product={p}
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

  const sections =
    view === "product" && productQuery.data
      ? buildProductSections(
          productQuery.data.products,
          assetClasses,
          displayConfig
        )
      : [];
  const platforms = platformQuery.data?.platforms ?? [];

  return (
    <div className="space-y-1" data-testid="portfolio-holdings">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">持仓明细</h3>
        {action}
      </div>

      {/* 视图分段切换（D-7）：持久化在 URL，由页面 onViewChange 回写 */}
      <div
        className="mt-2 grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1"
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
                ? "bg-white font-semibold text-slate-900 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
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
        sections.length === 0 ? (
          <div className="py-8 text-center text-muted-foreground">暂无持仓记录</div>
        ) : (
          <ProductSections
            sections={sections}
            snapshotDate={productQuery.data?.snapshot_date}
            variant={variant}
          />
        )
      ) : platforms.length === 0 ? (
        <div className="py-8 text-center text-muted-foreground">暂无持仓记录</div>
      ) : (
        <div className={`pt-3 ${cardGridClass(variant)}`}>
          {platforms.map((p) => (
            <HoldingPlatformCard
              key={p.platform_code ?? "unknown"}
              platform={p}
            />
          ))}
        </div>
      )}
    </div>
  );
}
