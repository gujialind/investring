"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Info } from "lucide-react";
import { formatReturnRate, getReturnColorClass } from "@/lib/utils";
import type { PortfolioPerformance } from "@/types/portfolio";

interface LedgerPerformanceProps {
  data?: PortfolioPerformance;
  variant?: "desktop" | "mobile";
}

/** 单个指标格（与 PerformanceMetrics 同形）：值 + 名称 + 可选说明气泡 */
function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: number | null | undefined;
  hint?: string;
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
      <div className={`mt-1 text-xl font-bold ${hasValue ? getReturnColorClass(value) : ""}`}>
        {hasValue ? formatReturnRate(value, 2, true) : "--"}
      </div>
    </div>
  );
}

const DIETZ_HINT =
  "Modified Dietz 按日折算：(期末市值 − 期初市值 − 区间净入金) / (期初市值 + 按发生时点加权的净入金)。" +
  "期初/期末市值取 ≤ 锚点最近一条记录（step-forward）；起点无市值记录或历史不足窗口期时不展示。";

/**
 * 台账型绩效面板（issue #905 PR3，P1=口径A 定案）。
 *
 * **只渲染**区间收益 return_1m/3m/6m/ytd/1y/3y（Modified Dietz）与年化 mwr（XIRR）。
 * TWR / 波动率 / 最大回撤（及峰谷日）/ initial_nav / current_nav / nav_series_consistent
 * 后端恒返 None，**一律不渲染**——台账稀疏记录无逐日净值序列，给值会把「没记」读成「没波动」
 * （口径B 已否决）。不新增 data_sparse 字段、不给指标加 blanket「仅供参考」标注；
 * 披露仅沿用现有 annualization_reliable 布尔位（持有期 <90 天 Alert，净值型同形先例）。
 */
export default function LedgerPerformance({ data, variant = "desktop" }: LedgerPerformanceProps) {
  const gridCls = variant === "mobile" ? "grid grid-cols-2 gap-2" : "grid grid-cols-2 gap-3";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">区间收益与年化</CardTitle>
        <CardDescription>
          台账型按市值与本金进出计算（Modified Dietz / XIRR），无净值序列、不展示 TWR/波动率/回撤
          {data?.holding_days ? `；持有 ${data.holding_days} 天` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 持有期过短：年化属大幅外推（沿用净值型 annualization_reliable 先例；台账无回撤，措辞不含回撤） */}
        {data && data.annualization_reliable === false && data.holding_days !== null && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              持有期仅 {data.holding_days} 天（不足 90 天），年化指标属于大幅外推，仅供参考。
            </AlertDescription>
          </Alert>
        )}

        <div className={gridCls}>
          <Metric label="近 1 月" value={data?.return_1m} hint={DIETZ_HINT} />
          <Metric label="近 3 月" value={data?.return_3m} hint={DIETZ_HINT} />
          <Metric label="近 6 月" value={data?.return_6m} hint={DIETZ_HINT} />
          <Metric label="今年以来" value={data?.return_ytd} hint={DIETZ_HINT} />
          <Metric label="近 1 年" value={data?.return_1y} hint={DIETZ_HINT} />
          <Metric label="近 3 年" value={data?.return_3y} hint={DIETZ_HINT} />
          <Metric
            label="MWR 年化"
            value={data?.mwr}
            hint="资金加权收益率（XIRR）：把每笔本金进出按发生时点计入现金流、加期末市值求解年化回报，衡量实际投入的钱赚了多少；短期大额进出会使其大幅失真。"
          />
        </div>

        {data && data.cash_flow_count > 0 && (
          <div className="text-xs text-muted-foreground">计入本金进出 {data.cash_flow_count} 笔</div>
        )}
      </CardContent>
    </Card>
  );
}