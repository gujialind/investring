"use client";

import { IN_TRANSIT_COLOR } from "@/lib/colors";
import { formatCurrency } from "@/lib/utils";

/**
 * 在途资金聚合卡（#595 评审决策补回，承接旧版独立在途卡）：
 * row1=在途资金（在途色点）+ 市值；row2=行级占比（与产品卡同一最大余数法行集，§4）。
 * 数据源为 by-product 响应的 in_transit_market_value（在途计市值、已含于 total）。
 */
export default function HoldingInTransitCard({
  marketValue,
  percent,
}: {
  marketValue: number;
  /** 行级占比（百分数，来自 buildCardPercentMap，含全部产品卡与在途卡） */
  percent: number;
}) {
  return (
    <div
      data-testid="holding-intransit-card"
      className="rounded-lg border border-border bg-card px-4 py-3"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-center text-sm font-semibold">
          <span
            className="mr-1.5 h-2 w-2 rounded-sm"
            style={{ background: IN_TRANSIT_COLOR }}
          />
          在途资金
        </span>
        <span className="text-lg font-bold number-cell flex-shrink-0 text-foreground">
          {formatCurrency(marketValue)}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-end">
        <span className="text-xs number-cell text-muted-foreground">
          占比 {percent.toFixed(1)}%
        </span>
      </div>
    </div>
  );
}
