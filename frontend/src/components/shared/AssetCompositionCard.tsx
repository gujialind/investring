"use client";

import { useState } from "react";
import AssetAllocationBar from "@/components/charts/AssetAllocationBar";
import AssetAllocationPie from "@/components/charts/AssetAllocationPie";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useChartTheme } from "@/hooks/useChartTheme";
import { sortAllocationItems, type AllocationItem } from "@/lib/allocation";
import { cn } from "@/lib/utils";

interface AssetCompositionCardProps {
  items: AllocationItem[];
  variant?: "desktop" | "mobile";
  forceColumn?: boolean;
  pieHeight?: number;
}

export default function AssetCompositionCard({
  items,
  variant = "desktop",
  forceColumn = false,
  pieHeight = 180,
}: AssetCompositionCardProps) {
  const [view, setView] = useState<"bar" | "pie">("bar");
  const theme = useChartTheme();
  const presentationItems = sortAllocationItems(items, theme);
  const mobile = variant === "mobile";

  return (
    <Card data-testid="asset-composition-card">
      <CardContent className={mobile ? "p-4" : "pt-6"}>
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium">资产分布</h3>
          <div
            role="group"
            aria-label="资产构成视图"
            className="flex shrink-0 rounded-md bg-muted p-0.5"
          >
            <Button
              type="button"
              variant="ghost"
              aria-pressed={view === "bar"}
              data-testid="asset-composition-view-bar"
              onClick={() => setView("bar")}
              className={cn(
                "px-2 text-xs",
                mobile ? "h-9" : "h-8",
                view === "bar" && "bg-card text-foreground shadow-sm"
              )}
            >
              条形图
            </Button>
            <Button
              type="button"
              variant="ghost"
              aria-pressed={view === "pie"}
              data-testid="asset-composition-view-pie"
              onClick={() => setView("pie")}
              className={cn(
                "px-2 text-xs",
                mobile ? "h-9" : "h-8",
                view === "pie" && "bg-card text-foreground shadow-sm"
              )}
            >
              饼图
            </Button>
          </div>
        </div>
        {view === "bar" ? (
          <AssetAllocationBar items={presentationItems} />
        ) : (
          <AssetAllocationPie
            items={presentationItems}
            height={pieHeight}
            forceColumn={forceColumn || mobile}
          />
        )}
      </CardContent>
    </Card>
  );
}
