"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Briefcase, Coins, TrendingUp, Users, Wallet } from "lucide-react";
import { formatCurrency, formatReturnRate, getReturnColorClass, getNumberCellClass, cn } from "@/lib/utils";

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
}

/**
 * 首页 4 项统计卡片（总资产/平均收益/活跃组合/投资人）。
 * 桌面端与移动端共用同一数据，仅布局通过 variant 区分。
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
    },
    {
      icon: Coins,
      iconCls: getReturnColorClass(totalProfit),
      id: "total_profit",
      label: "总收益",
      value: formatCurrency(totalProfit),
      valueCls: getReturnColorClass(totalProfit),
      sub: variant === "mobile" ? "市值−净投入·含台账型" : "市值−净投入（含台账型）",
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
              <div
                className={`text-lg font-bold ${item.valueCls}`}
                data-testid={`dashboard-stat-${item.id}`}
              >
                {item.value}
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
    // #904 P0b 起主数值走 font-mono（15 字符墨迹 216.05px，与现金流水页同）。
    // #905 PR3 增「总收益」卡共 5 卡：3 列需 (W−32)/3≥216→W≥680，取 #912 的 848 档；
    // 5 列需 (W−64)/5≥216→W≥1144。whitespace-nowrap 下窄于阈值会降列数而非挤压墨迹。
    <div className="@container">
      <div
        className="grid gap-4 md:grid-cols-2 @min-[848px]:grid-cols-3 @min-[1144px]:grid-cols-5"
        data-testid="dashboard-stat-grid"
      >
        {items.map((item) => (
          <Card key={item.label}>
            <CardContent className="pt-6">
              <div className="flex flex-row items-center justify-between pb-2">
                <span className="text-xs text-muted-foreground">{item.label}</span>
                <item.icon className={`h-4 w-4 ${item.iconCls}`} />
              </div>
              <div
                className={cn(getNumberCellClass(), "text-left whitespace-nowrap text-2xl font-semibold", item.valueCls)}
                data-testid={`dashboard-stat-${item.id}`}
              >
                {item.value}
              </div>
              <p className="text-xs text-muted-foreground mt-1">{item.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
