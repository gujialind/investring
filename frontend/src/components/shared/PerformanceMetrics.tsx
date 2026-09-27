"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { AlertTriangle, Info } from "lucide-react";
import { formatReturnRate, formatNav, getReturnColorClass } from "@/lib/utils";
import type { PortfolioPerformance } from "@/types/portfolio";

interface PerformanceMetricsProps {
  data?: PortfolioPerformance;
  variant?: "desktop" | "mobile";
}

/** 单个指标格：值 + 名称 + 可选说明气泡 */
function Metric({
  label,
  value,
  hint,
  colored = true,
  suffix,
}: {
  label: string;
  value: number | null | undefined;
  hint?: string;
  /** 是否按涨跌着色（回撤/波动率等非收益指标不着色） */
  colored?: boolean;
  suffix?: string;
}) {
  const hasValue = value !== null && value !== undefined;
  return (
    <div className="p-3 border rounded-lg">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {hint && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Info className="h-3 w-3 cursor-help" />
              </TooltipTrigger>
              <TooltipContent className="max-w-[260px]">{hint}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      <div
        className={`mt-1 text-xl font-bold ${
          hasValue && colored ? getReturnColorClass(value) : ""
        }`}
      >
        {hasValue ? `${formatReturnRate(value, 2, colored)}${suffix || ""}` : "--"}
      </div>
    </div>
  );
}

/**
 * 组合绩效指标面板（issue #99）：6 项指标。
 *
 * 近6月/近1年/近3年（历史不足窗口期时为 null → “--”）、MWR 年化、最大回撤、
 * 年化波动率。后端仍返回 1m/3m/ytd/twr 等字段，仅 UI 不展示。
 * #595：桌面端宿主改为详情页右栏（360 固定），栅格收敛为 2 列——
 * 原 lg:grid-cols-6 按视口判定，在窄栏内会把指标名挤成 CJK 竖排。
 */
export default function PerformanceMetrics({ data, variant = "desktop" }: PerformanceMetricsProps) {
  const gridCls =
    variant === "mobile"
      ? "grid grid-cols-2 gap-2"
      : "grid grid-cols-2 gap-3";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">绩效指标</CardTitle>
        <CardDescription>
          区间收益与风险指标，历史不足窗口期的区间收益不展示
          {data?.holding_days ? `；持有 ${data.holding_days} 天` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 净值序列自检失败：说明快照有断层或净值异常，指标可能不可信 */}
        {data?.nav_series_consistent === false && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              净值序列一致性校验未通过（两种 TWR 算法结果不等），请检查快照是否存在断层或异常净值。
            </AlertDescription>
          </Alert>
        )}

        {/* 持有期过短：年化属大幅外推，MWR 尤其容易失真（短期大额申购可换算出数百％） */}
        {data && data.annualization_reliable === false && data.holding_days !== null && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              持有期仅 {data.holding_days} 天（不足 90 天），年化指标属于大幅外推，仅供参考；累计收益与回撤不受影响。
            </AlertDescription>
          </Alert>
        )}

        <div className={gridCls}>
          <Metric
            label="近 6 月"
            value={data?.return_6m}
            hint="以 6 个月前当日或之后首个快照净值为基准；组合成立不足 6 个月时不展示"
          />
          <Metric
            label="近 1 年"
            value={data?.return_1y}
            hint="以 1 年前当日或之后首个快照净值为基准；组合成立不足 1 年时不展示"
          />
          <Metric
            label="近 3 年"
            value={data?.return_3y}
            hint="以 3 年前当日或之后首个快照净值为基准；组合成立不足 3 年时不展示"
          />
          <Metric
            label="MWR 年化"
            value={data?.mwr}
            hint="资金加权收益率（XIRR）：把每笔申赎按发生时点计入现金流求解年化回报，衡量实际投入的钱赚了多少；短期大额申购会使其大幅失真。"
          />
          <Metric
            label="最大回撤"
            value={data?.max_drawdown === null || data?.max_drawdown === undefined ? null : -data.max_drawdown}
            hint={
              data?.max_drawdown_peak_date
                ? `历史最高净值到之后最低点的最大跌幅（${data.max_drawdown_peak_date} → ${data.max_drawdown_trough_date}）`
                : "历史最高净值到之后最低点的最大跌幅"
            }
          />
          <Metric
            label="年化波动率"
            value={data?.annualized_volatility}
            colored={false}
            hint="日净值收益率的标准差按 252 交易日年化。数值越大净值波动越剧烈。"
          />
        </div>

        {(data?.initial_nav || data?.current_nav) && (
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
            <span>期初净值 {formatNav(data.initial_nav)}</span>
            <span>当前净值 {formatNav(data.current_nav)}</span>
            <span>计入现金流 {data.cash_flow_count} 笔</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
