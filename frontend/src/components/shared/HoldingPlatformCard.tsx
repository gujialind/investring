"use client";

import { ChevronRight } from "lucide-react";
import type { HoldingPlatformAggregate } from "@/types/holding";
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  getReturnColorClass,
} from "@/lib/utils";

/** 带符号货币（平台卡「持有收益 +¥1,780.00」形态，对齐 M2/D2 设计稿） */
function formatSignedCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "--";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}¥${formatNumber(Math.abs(value))}`;
}

/**
 * #595 按平台视图的平台卡（M2/D2）：
 * row1=平台名 + 占比 + 箭头；row2=持仓市值大数字；
 * row3=持有收益（红涨绿跌）+「N 只产品 · 现金 ¥X」（现金为 0 时省略现金段）。
 * 点击跳转平台详情页由 #595 步骤④接线，本组件保持纯展示。
 */
export default function HoldingPlatformCard({
  platform,
}: {
  platform: HoldingPlatformAggregate;
}) {
  return (
    <div
      data-testid="holding-platform-card"
      className="bg-white border border-slate-200 rounded-lg px-4 py-3"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold truncate">
          {platform.platform_name || platform.platform_code || "--"}
        </span>
        <span className="flex items-center text-xs text-slate-500 tabular-nums flex-shrink-0">
          {formatPercent(platform.ratio, 1, false)}
          <ChevronRight className="ml-0.5 h-3.5 w-3.5 text-slate-400" />
        </span>
      </div>
      <div className="mt-2 text-xs text-slate-500">持仓市值</div>
      <div className="mt-0.5 text-lg font-bold tabular-nums text-slate-900">
        {formatCurrency(platform.market_value)}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span
          className={`text-sm tabular-nums truncate ${getReturnColorClass(
            platform.holding_profit
          )}`}
        >
          持有收益 {formatSignedCurrency(platform.holding_profit)}
        </span>
        <span className="text-xs text-slate-500 tabular-nums flex-shrink-0">
          {platform.product_count} 只产品
          {platform.cash_balance > 0 &&
            ` · 现金 ${formatCurrency(platform.cash_balance, 0)}`}
        </span>
      </div>
    </div>
  );
}
