"use client";

import { ChevronRight } from "lucide-react";
import type { HoldingPlatformAggregate } from "@/types/holding";
import {
  formatCurrency,
  formatNumber,
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
 * row3=持有收益（红涨绿跌）+「N 只产品 · 现金 ¥X」（现金为 0 时省略现金段，
 * 负现金真实存量不省略，按 §12 符号内显式展示）。
 * 点击跳转平台详情页由 #595 步骤④接线，本组件保持纯展示。
 */
export default function HoldingPlatformCard({
  platform,
  percent,
}: {
  platform: HoldingPlatformAggregate;
  /** 行级占比（百分数，视图内最大余数法分配，§4） */
  percent: number;
}) {
  return (
    <div
      data-testid="holding-platform-card"
      className="rounded-lg border border-border bg-card px-4 py-3"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold truncate">
          {platform.platform_name || platform.platform_code || "--"}
        </span>
        <span className="flex items-center text-xs number-cell flex-shrink-0 text-muted-foreground">
          {percent.toFixed(1)}%
          <ChevronRight className="ml-0.5 h-3.5 w-3.5 text-muted-foreground" />
        </span>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">持仓市值</div>
      <div className="mt-0.5 text-lg font-bold number-cell text-foreground">
        {formatCurrency(platform.market_value)}
      </div>
      {/* flex-wrap：窄卡（移动 1 列）下「持有收益 +¥3,000.00」+「N 只产品 · 现金 ¥40,000」
          一行放不下，整段换行不截断——金融数值不可 ellipsis（#636 目检实证） */}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        {/* 左对齐明细与产品卡 row3 同口径用 tabular-nums：number-cell 的 mono 字形更宽，
            窄卡（移动 1 列）下会把「+¥3,000.00」截成 +¥3,00…，收益值不可截断 */}
        <span
          className={`text-sm tabular-nums truncate ${getReturnColorClass(
            platform.holding_profit
          )}`}
        >
          持有收益 {formatSignedCurrency(platform.holding_profit)}
        </span>
        <span className="text-xs text-muted-foreground number-cell flex-shrink-0">
          {platform.product_count} 只产品
          {platform.cash_balance !== 0 &&
            ` · 现金 ${formatCurrency(platform.cash_balance, 0)}`}
        </span>
      </div>
    </div>
  );
}
