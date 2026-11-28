"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Text,
  type XAxisTickContentProps,
} from "recharts";
import { chartOtherColor, navLineColor } from "@/lib/colors";
import { useChartTheme } from "@/hooks/useChartTheme";
import { formatNav } from "@/lib/utils";

interface NavPoint {
  date: string;
  nav: number;
}

interface NavCurveProps {
  data: NavPoint[];
  height?: number;
  initialNav?: number;
}

/** 首末日期向内锚定，8px 右边距下仍完整可见；日期格式与筛选语义不变。 */
function NavDateTick({
  x,
  y,
  payload,
  index,
  visibleTicksCount,
  tickFormatter,
  verticalAnchor,
}: XAxisTickContentProps) {
  let textAnchor: "start" | "middle" | "end" = "middle";
  if (index === 0) textAnchor = "start";
  else if (index === visibleTicksCount - 1) textAnchor = "end";
  return (
    <Text
      x={x}
      y={y}
      textAnchor={textAnchor}
      verticalAnchor={verticalAnchor}
      fontSize={12}
      fill="hsl(var(--foreground-secondary))"
      stroke="none"
      className="recharts-cartesian-axis-tick-value"
    >
      {tickFormatter ? tickFormatter(payload.value, index) : String(payload.value)}
    </Text>
  );
}

export default function NavCurve({ data, height = 300, initialNav = 1.0 }: NavCurveProps) {
  const theme = useChartTheme();
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center text-muted-foreground" style={{ height }}>
        暂无净值数据
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart
        data={data}
        className="nav-curve"
        margin={{ top: 5, right: 8, left: 0, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        {/* #872 PR2：recharts 默认轴色 #666 暗卡仅 2.91:1，显式走 token（visual-spec §2 治理结论） */}
        <XAxis
          dataKey="date"
          stroke="hsl(var(--border))"
          tick={NavDateTick}
          tickFormatter={(value) => String(value).slice(5)}
        />
        <YAxis
          domain={["auto", "auto"]}
          width="auto"
          stroke="hsl(var(--border))"
          tick={{ fontSize: 12, fill: "hsl(var(--foreground-secondary))" }}
          tickFormatter={(value) => formatNav(value)}
        />
        <Tooltip
          formatter={(value) => [formatNav(Number(value)), "净值"]}
          labelFormatter={(label) => `日期: ${label}`}
          contentStyle={{
            backgroundColor: "hsl(var(--popover))",
            border: "1px solid hsl(var(--border))",
            color: "hsl(var(--popover-foreground))",
          }}
          labelStyle={{ color: "hsl(var(--foreground-secondary))" }}
          itemStyle={{ color: "hsl(var(--popover-foreground))" }}
        />
        <ReferenceLine
          y={initialNav}
          fill="none"
          stroke={chartOtherColor(theme)}
          strokeDasharray="5 5"
          label={{
            value: "初始净值",
            position: "insideRight",
            offset: 8,
            fontSize: 12,
            fill: "hsl(var(--foreground-secondary))",
          }}
        />
        <Line
          type="monotone"
          dataKey="nav"
          stroke={navLineColor(theme)}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
          name="组合净值"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}