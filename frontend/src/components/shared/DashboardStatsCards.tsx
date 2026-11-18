"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Briefcase, TrendingUp, Users, Wallet } from "lucide-react";
import {
  formatCurrency,
  formatReturnRate,
  getReturnColorClass,
  getReturnBgClass,
  getReturnSoftTextClass,
  getNumberCellClass,
  cn,
} from "@/lib/utils";

interface DashboardStatsCardsProps {
  totalValue: number;
  totalProfit: number;
  avgReturn: number;
  activeCount: number;
  totalCount: number;
  investorCount: number;
  variant?: "desktop" | "mobile";
}

interface StatItem {
  icon: typeof Wallet;
  iconCls: string;
  /** 稳定标识：label 随 variant 变化，不能当测试锚点 */
  id: string;
  label: string;
  value: string;
  valueCls: string;
  sub: string;
  /** 总资产卡挂「总收益」chip（R8 跨口径合并，#905 PR3；沿用 #904 P0b chip 惯例） */
  chip?: number | null;
}

/**
 * 首页 4 项统计卡片（总资产[+总收益chip]/平均收益/活跃组合/投资人）。
 * 桌面端与移动端共用同一数据，仅布局通过 variant 区分。
 *
 * R8（#905 PR3）：总资产/总收益为净值型+台账型跨口径合并（后端 list 已按 kind 派生）。
 * 总收益以 chip 挂在总资产卡（不另占第 5 卡）——保持 #774/#904 的「宽视口四列单行、
 * 8 位带符号金额不碰墨迹」栅格守护（regression.spec.ts）：5 卡会在 1280 档被迫降列/换行。
 * 平均收益率仅净值型集合（a 口径），sub 明标「不含台账型」，不静默漏分母。
 */
export default function DashboardStatsCards({
  totalValue,
  totalProfit,
  avgReturn,
  activeCount,
  totalCount,
  investorCount,
  variant = "desktop",
}: DashboardStatsCardsProps) {
  const items: StatItem[] = [
    {
      icon: Wallet,
      iconCls: "text-muted-foreground",
      id: "total_value",
      label: "总资产",
      value: formatCurrency(totalValue),
      valueCls: "",
      sub: variant === "mobile" ? `${activeCount} 个活跃·含台账型` : `${activeCount} 个活跃组合合计（含台账型）`,
      chip: totalProfit,
    },
    {
      icon: TrendingUp,
      iconCls: getReturnColorClass(avgReturn),
      id: "avg_return",
      label: variant === "mobile" ? "平均收益" : "平均累计收益",
      value: formatReturnRate(avgReturn),
      valueCls: getReturnColorClass(avgReturn),
      sub: variant === "mobile" ? "净值型·不含台账型" : "净值型平均（不含台账型）",
    },
    {
      icon: Briefcase,
      iconCls: "text-muted-foreground",
      id: "active_count",
      label: "活跃组合",
      value: String(activeCount),
      valueCls: "",
      sub: `共 ${totalCount} 个组合`,
    },
    {
      icon: Users,
      iconCls: "text-muted-foreground",
      id: "investor_count",
      label: "投资人",
      value: String(investorCount),
      valueCls: "",
      sub: variant === "mobile" ? "系统注册" : "系统注册投资人",
    },
  ];

  const chipOf = (chip: number | null | undefined) =>
    chip === null || chip === undefined ? null : (
      <span
        data-testid="dashboard-stat-total_profit"
        title="总收益（含台账型，市值−净投入）"
        className={cn(
          "inline-flex items-center rounded-full px-2 text-xs font-medium",
          getReturnBgClass(chip),
          getReturnSoftTextClass(chip),
        )}
      >
        {chip > 0 ? "+" : ""}
        {formatCurrency(chip)}
      </span>
    );

  if (variant === "mobile") {
    return (
      <div className="grid grid-cols-2 gap-3" data-testid="dashboard-stat-grid">
        {items.map((item) => (
          <Card key={item.label}>
            <CardContent className="p-3">
              <div className="flex items-center gap-2 mb-1">
                <item.icon className={`h-4 w-4 ${item.iconCls}`} />
                <span className="text-xs text-muted-foreground">{item.label}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div
                  className={`text-lg font-bold tabular-nums ${item.valueCls}`}
                  data-testid={`dashboard-stat-${item.id}`}
                >
                  {item.value}
                </div>
                {chipOf(item.chip)}
              </div>
              <p className="text-xs text-muted-foreground mt-1">{item.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    // 桌面列数按**容器宽**取（#774，口径与阈值同 PortfolioStatsCards，推导见该处注释）：
    // #904 P0b 起主数值走 font-mono（15 字符墨迹 216.05px，与现金流水页同），
    // 四列不相碰要求容器 ≥ 4×(216.05 − 16) + 3×16 ≈ 848。总收益走 chip 不占第 5 卡，
    // 维持四列单行（regression.spec.ts 宽视口守护）。
    <div className="@container">
      <div
        className="grid gap-4 md:grid-cols-2 @min-[848px]:grid-cols-4"
        data-testid="dashboard-stat-grid"
      >
        {items.map((item) => (
          <Card key={item.label}>
            <CardContent className="pt-6">
              <div className="flex flex-row items-center justify-between pb-2">
                <span className="text-xs text-muted-foreground">{item.label}</span>
                <item.icon className={`h-4 w-4 ${item.iconCls}`} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div
                  className={cn(getNumberCellClass(), "text-left whitespace-nowrap text-2xl font-semibold", item.valueCls)}
                  data-testid={`dashboard-stat-${item.id}`}
                >
                  {item.value}
                </div>
                {chipOf(item.chip)}
              </div>
              <p className="text-xs text-muted-foreground mt-1">{item.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}