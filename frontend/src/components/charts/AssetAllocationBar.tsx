"use client";

import type { AllocationItem } from "@/lib/allocation";
import { formatCurrency } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface AssetAllocationBarProps {
  items: AllocationItem[];
}

const ALLOCATION_GRID_COLUMNS = "grid-cols-[4rem_minmax(1.5rem,1fr)_4rem_6rem]";

export default function AssetAllocationBar({ items }: AssetAllocationBarProps) {
  if (!items.length) {
    return (
      <div className="flex min-h-24 items-center justify-center text-sm text-muted-foreground">
        暂无持仓数据
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div data-testid="asset-allocation-bar">
        <div
          className={`mb-1 grid ${ALLOCATION_GRID_COLUMNS} items-center gap-0.5 text-xs text-muted-foreground`}
          aria-hidden="true"
        >
          <span>分类</span>
          <span className="flex min-w-6 justify-between">
            <span>0%</span>
            <span>100%</span>
          </span>
          <span className="text-right">占比</span>
          <span className="text-right">金额</span>
        </div>
        <div className="space-y-1">
          {items.map((item) => {
            const percent = `${item.percent.toFixed(1)}%`;
            const amount = formatCurrency(item.value);
            const meterPercent = Math.min(100, Math.max(0, item.percent));
            return (
              <Tooltip key={item.code}>
                <TooltipTrigger asChild>
                  <div
                    tabIndex={0}
                    role="meter"
                    aria-label={`${item.key}占比`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={meterPercent}
                    aria-valuetext={`${percent}，${amount}`}
                    data-testid="asset-allocation-row"
                    data-allocation-code={item.code}
                    className={`grid w-full ${ALLOCATION_GRID_COLUMNS} items-center gap-0.5 rounded-sm py-2 text-sm outline-none transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card`}
                  >
                    <span className="min-w-0 truncate text-foreground-secondary">
                      {item.key}
                    </span>
                    <span
                      aria-hidden="true"
                      data-testid="asset-allocation-track"
                      className="h-2 min-w-6 overflow-hidden rounded-full bg-muted"
                    >
                      <span
                        data-testid="asset-allocation-fill"
                        className="block h-full rounded-full"
                        style={{ width: `${meterPercent}%`, backgroundColor: item.color }}
                      />
                    </span>
                    <span className="text-right font-semibold tabular-nums">
                      {percent}
                    </span>
                    <span className="min-w-0 truncate text-right tabular-nums">
                      {amount}
                    </span>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="top">
                  <div className="font-medium">{item.key}</div>
                  <div className="mt-1 tabular-nums">
                    {amount} · {percent}
                  </div>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
      </div>
    </TooltipProvider>
  );
}
