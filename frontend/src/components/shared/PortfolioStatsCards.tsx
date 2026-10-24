"use client";

import { Card, CardContent } from "@/components/ui/card";
import { CalendarDays, Coins, TrendingUp, Wallet } from "lucide-react";
import { formatNav, formatNumber, getReturnColorClass } from "@/lib/utils";

interface PortfolioStatsCardsProps {
  /** 总资产（元）：最新快照 total_value，无快照（draft）传 null */
  totalValue: number | null;
  /** 组合净值：最新快照 unit_price，无快照传 null */
  unitPrice: number | null;
  /** 累计收益（元，红涨绿跌）：总资产 − 净投入，无快照传 null */
  totalProfit: number | null;
  /** 运行天数：首末快照间隔，无快照传 null */
  holdingDays: number | null;
  /** desktop: 标题左/图标右的大卡片；mobile: 2×2 紧凑卡片 */
  variant?: "desktop" | "mobile";
}

function formatProfitAmount(value: number | null): string {
  if (value === null || value === undefined) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)}`;
}

/**
 * 组合详情页 4 项统计卡片（issue #99）：
 * 总资产（元）/ 净值 / 累计收益（元，红涨绿跌）/ 运行天数。
 * 桌面端与移动端共用同一数据源，仅布局通过 variant 区分。
 */
export default function PortfolioStatsCards({
  totalValue,
  unitPrice,
  totalProfit,
  holdingDays,
  variant = "desktop",
}: PortfolioStatsCardsProps) {
  const items = [
    {
      key: "总资产",
      id: "total_value",
      icon: Wallet,
      value: totalValue === null ? "--" : formatNumber(totalValue),
      valueCls: "",
    },
    {
      key: "净值",
      id: "nav",
      icon: TrendingUp,
      value: unitPrice === null ? "--" : formatNav(unitPrice),
      valueCls: "",
    },
    {
      key: "累计收益",
      id: "profit",
      icon: Coins,
      value: formatProfitAmount(totalProfit),
      valueCls: totalProfit === null ? "" : getReturnColorClass(totalProfit),
    },
    {
      key: "运行天数",
      id: "holding_days",
      icon: CalendarDays,
      value: holdingDays === null ? "--" : `${formatNumber(holdingDays, 0)} 天`,
      valueCls: "",
    },
  ];

  if (variant === "mobile") {
    return (
      <div className="grid grid-cols-2 gap-3" data-testid="portfolio-stat-grid">
        {items.map((item) => (
          <Card key={item.key}>
            <CardContent className="p-3">
              <div className="mb-1 flex items-center gap-2">
                <item.icon className="h-4 w-4 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">{item.key}</span>
              </div>
              <div
                className={`text-lg font-bold tabular-nums ${item.valueCls}`}
                data-testid={`portfolio-stat-${item.id}`}
              >
                {item.value}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    // 桌面列数按**容器宽**取（#774，口径同 #772 的现金流水页概览卡）：主数值是不在串内断行的
    // 单行墨迹（15 字符实测 205px），而 grid 轨道 minmax(0,1fr) 可被压到墨迹之下 → 邻卡数字
    // 相碰（1024 档实测 −11px）。同排墨迹间隙 = 轨道 + gap-4 − 墨迹（卡自带的边框与 p-6 在
    // 相邻两侧相消），故四列不相碰要求容器 ≥ 4×(205 − 16) + 3×16 = 804：与现金流水页的 848
    // 是同一条公式，差只在墨迹宽（那边主数值走 font-mono，同串 216.05px）。
    // @container 须挂在栅格的**父**元素上：容器查询按最近祖先容器求值，挂在栅格自身不生效。
    <div className="@container">
      <div
        className="grid gap-4 md:grid-cols-2 @min-[804px]:grid-cols-4"
        data-testid="portfolio-stat-grid"
      >
        {items.map((item) => (
          <Card key={item.key}>
            <CardContent className="pt-6">
              <div className="flex flex-row items-center justify-between pb-2">
                <span className="text-sm font-medium">{item.key}</span>
                <item.icon className="h-4 w-4 text-muted-foreground" />
              </div>
              <div
                className={`text-2xl font-bold tabular-nums ${item.valueCls}`}
                data-testid={`portfolio-stat-${item.id}`}
              >
                {item.value}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
