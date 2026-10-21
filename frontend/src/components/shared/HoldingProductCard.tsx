"use client";

import type { HoldingProductAggregate } from "@/types/holding";
import { CASH_PRODUCT_CODE } from "@/lib/allocation";
import {
  formatCurrency,
  formatMarketName,
  formatNumber,
  formatProductCodeLabel,
  getReturnColorClass,
} from "@/lib/utils";

/** 带符号收益（卡片「累计收益/最新收益」列：纯数字不带 ¥，对齐 M1/D1 设计稿） */
function formatSignedNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)}`;
}

/** 现金聚合卡的平台副标题：单平台显名，多平台显数量（设计稿样本为单平台形态） */
function cashSubtitle(product: HoldingProductAggregate): string {
  const platforms = product.platforms;
  if (platforms.length === 1) {
    return `可用现金 · ${platforms[0].platform_name || platforms[0].platform_code || "--"}`;
  }
  return `可用现金 · ${platforms.length} 个平台`;
}

/**
 * #595 按产品视图的持仓产品卡（跨平台聚合，M1/D1）：
 * row1=产品名 + 市值大数字；row2=代码 · 市场标签 + 占比（行级最大余数法，§4）；
 * row3 两列=累计收益（D-3：持有收益口径）| 最新收益(MM/DD)。
 * 偏离 M1/D1 的两项（#724；理由、实测列宽与溢出代价形态由 visual-spec §12 持有）：
 * 「持有份额」列双端移除，份额改由两个消费者各自的详情页概览卡承载——组合「按产品」卡
 * → `ProductDetailContent::overview`（跨平台聚合份额），平台详情页卡（PlatformDetailContent）
 * → `PlatformProductDetailContent::overview`（本平台切片份额），整卡可点即达；row3 收益值
 * `whitespace-nowrap` 不截断（#636 金融数值不可 ellipsis）。
 * 现金为聚合卡（无 row3，副标题为可用现金 · 平台）。
 * 点击跳转产品详情页由 #595 步骤③接线，本组件保持纯展示。
 */
export default function HoldingProductCard({
  product,
  percent,
  snapshotDate,
}: {
  product: HoldingProductAggregate;
  /** 行级占比（百分数，来自 buildCardPercentMap 最大余数法，含在途同一行集） */
  percent: number;
  snapshotDate?: string | null;
}) {
  const isCash = product.product_code === CASH_PRODUCT_CODE;
  // #705 row2 代码用显示码；港互认双码并列（isCash 判定与跳转仍用原始 product_code）
  const codeLabel = formatProductCodeLabel(
    product.display_code,
    product.product_code,
    product.market
  );
  const mmdd = snapshotDate ? snapshotDate.slice(5, 10).replace("-", "/") : "";
  return (
    <div
      data-testid="holding-product-card"
      className="rounded-lg border border-border bg-card px-4 py-3"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold truncate">
          {product.product_name || codeLabel}
        </span>
        <span className="text-lg font-bold number-cell flex-shrink-0 text-foreground">
          {formatCurrency(product.market_value)}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground truncate">
          {isCash
            ? cashSubtitle(product)
            : `${codeLabel} · ${formatMarketName(product.market)}`}
        </span>
        <span className="text-xs number-cell flex-shrink-0 text-muted-foreground">
          占比 {percent.toFixed(1)}%
        </span>
      </div>
      {!isCash && (
        <div className="mt-2.5 flex border-t border-border pt-2.5">
          <div className="flex-1 min-w-0">
            <div className="text-xs text-muted-foreground">累计收益</div>
            <div
              data-testid="total-profit-value"
              className={`mt-0.5 text-sm font-bold tabular-nums whitespace-nowrap ${getReturnColorClass(
                product.holding_profit
              )}`}
            >
              {formatSignedNumber(product.holding_profit)}
            </div>
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-muted-foreground">
              最新收益{mmdd ? `(${mmdd})` : ""}
            </div>
            <div
              data-testid="daily-profit-value"
              className={`mt-0.5 text-sm font-bold tabular-nums whitespace-nowrap ${getReturnColorClass(
                product.daily_profit
              )}`}
            >
              {formatSignedNumber(product.daily_profit)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
