"use client";

import Link from "next/link";
import type { HoldingProductPlatformSlice } from "@/types/holding";
import {
  formatCurrency,
  formatSharesUnit,
  formatSignedCurrency,
  getReturnColorClass,
} from "@/lib/utils";

interface PlatformDistributionCardProps {
  /** 卡片标题（产品详情页 = "平台分布"，平台-产品详情页 = "全部平台持仓"） */
  title: string;
  slices: HoldingProductPlatformSlice[];
  /** 行点击跳转前缀：`${basePath}/${portfolioCode}/platforms/${platformCode}/products/${productCode}`；
   *  不传则纯展示（步骤③产品详情页的平台分布行无跳转） */
  rowLinkPrefix?: (slice: HoldingProductPlatformSlice) => string | undefined;
  testId?: string;
}

/**
 * #595 平台分布卡（共享组件）：
 * - 产品详情页 D3/M3：title="平台分布"，rowLinkPrefix 不传（步骤④接线后传入）
 * - 平台-产品详情页 D5/M5：title="全部平台持仓"，rowLinkPrefix 指向各平台的平台-产品详情
 * 每行：平台名 + 份额 + 持有/累计收益 + 市值 + 占产品比。
 */
export default function PlatformDistributionCard({
  title,
  slices,
  rowLinkPrefix,
  testId = "platform-distribution-card",
}: PlatformDistributionCardProps) {
  return (
    <section className="rounded-lg border border-border bg-card p-4" data-testid={testId}>
      <h3 className="text-base font-semibold">{title}</h3>
      {slices.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无平台持仓</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {slices.map((slice) => {
            const href = rowLinkPrefix?.(slice);
            const content = (
              <>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {slice.platform_name || slice.platform_code || "--"}
                  </div>
                  <div className="whitespace-nowrap text-sm text-muted-foreground tabular-nums">
                    {formatSharesUnit(slice.shares)}
                  </div>
                  <div className="flex flex-wrap gap-x-1 text-xs tabular-nums">
                    <span className={`whitespace-nowrap ${getReturnColorClass(slice.holding_profit)}`}>
                      持有 {formatSignedCurrency(slice.holding_profit)}
                    </span>
                    <span className="whitespace-nowrap text-muted-foreground">
                      · 累计 {formatSignedCurrency(slice.cumulative_profit)}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <div className="text-right">
                    <div className="text-sm font-medium number-cell">
                      {formatCurrency(slice.market_value)}
                    </div>
                    <div className="text-xs text-muted-foreground number-cell">
                      {slice.ratio_in_product !== null && slice.ratio_in_product !== undefined
                        ? `${(slice.ratio_in_product * 100).toFixed(1)}%`
                        : "--"}
                    </div>
                  </div>
                </div>
              </>
            );

            if (href) {
              return (
                <li key={slice.platform_code ?? "unknown"} data-testid="platform-distribution-row">
                  <Link
                    href={href}
                    className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {content}
                  </Link>
                </li>
              );
            }

            return (
              <li
                key={slice.platform_code ?? "unknown"}
                data-testid="platform-distribution-row"
                className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
              >
                {content}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
