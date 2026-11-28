"use client";

import Link from "next/link";
import type { HoldingProductAggregate } from "@/types/holding";
import { CASH_PRODUCT_CODE } from "@/lib/allocation";
import { CUMULATIVE_PROFIT_INFO_TIP, productCardKey } from "@/lib/holdings";
import {
  cn,
  formatCurrency,
  formatMarketName,
  formatNumber,
  formatProductCodeLabel,
  getReturnColorClass,
} from "@/lib/utils";
import InfoTip from "./InfoTip";

/** 移动产品行返回焦点的 sessionStorage 键前缀，后接组合码（仅组合页消费） */
export const MOBILE_PRODUCT_RETURN_PREFIX = "investring:mobile-product-return:";

/** 带符号收益（卡片「累计收益/最新收益」列：纯数字不带 ¥，对齐 M1/D1 设计稿） */
function formatSignedNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)}`;
}

/**
 * #595 按产品视图的持仓产品卡（跨平台聚合，M1/D1）：
 * row1=产品名 + 市值大数字；row2=代码 · 市场标签 + 占比（行级最大余数法，§4）；
 * row3 两列=累计收益（全历史净现金流口径，含已卖出实现盈亏）| 最新收益(MM/DD)。
 * 偏离 M1/D1 的两项（#724；理由、实测列宽与溢出代价形态由 visual-spec §12 持有）：
 * 「持有份额」列双端移除，份额改由两个消费者各自的详情页概览卡承载——组合「按产品」卡
 * → `ProductDetailContent::overview`（跨平台聚合份额），平台详情页卡（PlatformDetailContent）
 * → `PlatformProductDetailContent::overview`（本平台切片份额），整卡可点即达；row3 收益值
 * `whitespace-nowrap` 不截断（#636 金融数值不可 ellipsis）。
 * 现金为聚合卡（无 row3，双端副标只显「可用现金」，平台构成在产品详情页）。
 * 点击跳转产品详情页由 `href` 承载；链接为独立覆盖层，说明按钮不嵌套进链接。
 * `variant="mobile"` 仅去掉卡壳（组合「按产品」移动列表用分隔线行），字段与链接不变。
 */
export default function HoldingProductCard({
  product,
  percent,
  snapshotDate,
  href,
  variant = "desktop",
  returnFocusKey,
}: {
  product: HoldingProductAggregate;
  /** 行级占比（百分数，来自 buildCardPercentMap 最大余数法，含在途同一行集） */
  percent: number;
  snapshotDate?: string | null;
  /** 产品详情链接；卡内 InfoTip 与链接并列，避免嵌套交互元素 */
  href: string;
  variant?: "desktop" | "mobile";
  /** 移动行点击时记录 code-market 全键，供组合页返回后聚焦原行；传组合码即按组合隔离 */
  returnFocusKey?: string;
}) {
  const isCash = product.product_code === CASH_PRODUCT_CODE;
  const isMobileRow = variant === "mobile";
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
      className={cn(
        "isolate relative bg-card",
        isMobileRow
          ? "px-1 py-3"
          : "rounded-lg border border-border px-4 py-3",
      )}
    >
      <Link
        href={href}
        aria-label={`查看${product.product_name || codeLabel}详情`}
        data-product-code={product.product_code}
        data-product-key={productCardKey(product)}
        onClick={(event) => {
          if (
            !isMobileRow ||
            !returnFocusKey ||
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
          ) return;
          try {
            sessionStorage.setItem(
              `${MOBILE_PRODUCT_RETURN_PREFIX}${returnFocusKey}`,
              productCardKey(product),
            );
          } catch {
            // 存储不可用时放弃焦点恢复，不能阻断产品跳转。
          }
        }}
        className={cn(
          "absolute inset-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          !isMobileRow && "rounded-lg",
        )}
      />
      <div className="pointer-events-none relative">
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
              ? "可用现金"
              : `${codeLabel} · ${formatMarketName(product.market)}`}
          </span>
          <span className="text-xs number-cell flex-shrink-0 text-muted-foreground">
            占比 {percent.toFixed(1)}%
          </span>
        </div>
        {!isCash && (
          <div className="mt-2.5 flex border-t border-border pt-2.5">
            <div className="flex-1 min-w-0">
              {/* 覆盖链接下方的内容不接收指针；InfoTip 触点显式恢复命中与层级。 */}
              <div className="flex h-11 items-center gap-1 text-xs text-muted-foreground">
                <span>累计收益</span>
                <InfoTip
                  {...CUMULATIVE_PROFIT_INFO_TIP}
                  triggerClassName="pointer-events-auto relative z-10"
                />
              </div>
              <div
                data-testid="total-profit-value"
                className={`mt-0.5 text-sm font-bold tabular-nums whitespace-nowrap ${getReturnColorClass(
                  product.cumulative_profit
                )}`}
              >
                {formatSignedNumber(product.cumulative_profit)}
              </div>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex h-11 items-center text-xs text-muted-foreground">
                <span>最新收益{mmdd ? `(${mmdd})` : ""}</span>
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
    </div>
  );
}
