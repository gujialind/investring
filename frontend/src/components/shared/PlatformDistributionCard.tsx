"use client";

import Link from "next/link";
import type { HoldingProductPlatformSlice } from "@/types/holding";
import {
  formatCurrency,
  formatSharesUnit,
  formatSignedCurrency,
  getReturnColorClass,
  largestRemainderPercents,
} from "@/lib/utils";

interface PlatformDistributionCardProps {
  /** 卡片标题（产品详情页 = "平台分布"，平台-产品详情页 = "全部平台持仓"） */
  title: string;
  /** 完整平台切片集合（构成对同一市值的完整划分，行级占比经最大余数法分配，§4） */
  slices: HoldingProductPlatformSlice[];
  /** 行点击跳转回调：对某行返回 undefined ⇒ 该行纯展示（不可点）；
   *  href 必须带 ?market= 参数（后端按 (product_code, market) 双键取行）。
   *  两个调用点均传入此回调。 */
  rowLinkPrefix: (slice: HoldingProductPlatformSlice) => string | undefined;
  testId?: string;
}

/**
 * #595 平台分布卡（共享组件）：
 * - 产品详情页 D3/M3：title="平台分布"，行链接指向各平台的平台-产品详情
 * - 平台-产品详情页 D5/M5：title="全部平台持仓"，当前平台行不可点（rowLinkPrefix 返回 undefined）
 * 每行：平台名 + 份额 + 持有/累计收益 + 市值 + 占产品比（最大余数法，§4）。
 */
export default function PlatformDistributionCard({
  title,
  slices,
  rowLinkPrefix,
  testId = "platform-distribution-card",
}: PlatformDistributionCardProps) {
  // S9：行级占比经最大余数法分配，加总恒 100.0%（§4），禁止散装 toFixed(1)
  const percents = largestRemainderPercents(slices.map((s) => s.market_value));

  return (
    <section className="rounded-lg border border-border bg-card p-4" data-testid={testId}>
      <h3 className="text-lg font-semibold">{title}</h3>
      {slices.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无平台持仓</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {slices.map((slice, i) => {
            const href = rowLinkPrefix(slice);
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
                      {percents[i]?.toFixed(1) ?? "--"}%
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
