"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { AllocationItem } from "@/lib/allocation";
import { formatNumber } from "@/lib/utils";

interface AssetAllocationPieProps {
  /** 按资产大类聚合的市值数组（buildAllocation 输出，占比已走最大余数法） */
  items: AllocationItem[];
  height?: number;
  /** 强制竖排（饼在上、图例在下）：容器窄但视口宽时断点判断失真（#595 右栏 360 场景） */
  forceColumn?: boolean;
}

/**
 * 资产分布环形图（issue #99）：左侧 donut + 右侧图例（名称 + 占比%）。
 * 颜色/顺序由 asset_class 维度字典 sort_order 驱动（#128，见 lib/allocation.ts）。
 * 图例标签 whitespace-nowrap 防窄容器 CJK 逐字竖排（#355 同族防护）。
 */
export default function AssetAllocationPie({ items, height = 180, forceColumn = false }: AssetAllocationPieProps) {
  if (!items.length) {
    return (
      <div
        className="flex items-center justify-center text-muted-foreground"
        style={{ height }}
      >
        暂无持仓数据
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col items-center gap-4 ${
        forceColumn ? "" : "sm:flex-row sm:gap-10"
      }`}
    >
      <div className="shrink-0" style={{ width: height, height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={items}
              dataKey="value"
              nameKey="key"
              innerRadius="60%"
              outerRadius="100%"
              strokeWidth={0}
            >
              {items.map((item) => (
                <Cell key={item.key} fill={item.color} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value, name) => [
                `${formatNumber(Number(value))} 元`,
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="flex-1 grid grid-cols-2 gap-x-6 gap-y-3 w-full">
        {items.map((item) => (
          <div key={item.key} className="flex items-center text-sm">
            <span
              className="w-2 h-2 rounded-sm mr-2 shrink-0"
              style={{ background: item.color }}
            />
            <span className="text-slate-700 whitespace-nowrap">{item.key}</span>
            <span className="ml-auto font-semibold tabular-nums whitespace-nowrap">
              {item.percent.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
