"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatCurrency, getReturnColorClass } from "@/lib/utils";
import { ArrowLeft, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { usePositionList } from "@/hooks/usePosition";
import PositionCard from "@/components/shared/PositionCard";
import CashMarketValueUpdateDialog from "@/components/shared/dialogs/CashMarketValueUpdateDialog";
import type { Position } from "@/types/position";

export default function MobilePositionsPage() {
  const params = useParams();
  const code = params.code as string;

  const { data: positionsData, isLoading } = usePositionList(code);
  const positions: Position[] = positionsData?.items || [];

  // #595 §4.5：现金市值更新 Dialog 开关
  const [isCashUpdateOpen, setIsCashUpdateOpen] = useState(false);

  const totalMarketValue = positions.reduce((sum, p) => sum + (p.market_value || 0), 0);
  const totalCost = positions.reduce((sum, p) => sum + ((p.shares || 0) * (p.cost_price || 0)), 0);
  const totalProfitLoss = totalMarketValue - totalCost;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href={`/m/portfolio/${code}`}>
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div>
            <h1 className="text-2xl font-semibold">持仓管理</h1>
            <p className="text-xs text-muted-foreground">{code}</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          data-testid="cash-update-trigger"
          onClick={() => setIsCashUpdateOpen(true)}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-3 gap-2">
        <Card>
          <CardContent className="p-3 text-center">
            <div className="text-sm font-bold">{formatCurrency(totalMarketValue)}</div>
            <p className="text-xs text-muted-foreground mt-1">总市值</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3 text-center">
            <div className="text-sm font-bold">{formatCurrency(totalCost)}</div>
            <p className="text-xs text-muted-foreground mt-1">总成本</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-3 text-center">
            <div className={`text-sm font-bold ${getReturnColorClass(totalProfitLoss)}`}>
              {formatCurrency(totalProfitLoss)}
            </div>
            <p className="text-xs text-muted-foreground mt-1">总收益</p>
          </CardContent>
        </Card>
      </div>

      {/* Positions List */}
      <div className="space-y-3">
        {positions.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center text-muted-foreground">
              暂无持仓数据
            </CardContent>
          </Card>
        ) : (
          positions.map((position) => (
            <PositionCard
              key={position.id}
              productCode={position.product_code}
              productName={position.product_name}
              market={position.market}
              shares={position.shares}
              costPrice={position.cost_price}
              currentPrice={position.unit_price}
              marketValue={position.market_value}
              profitLoss={position.profit_loss}
              profitLossPercent={position.profit_loss_percent}
            />
          ))
        )}
      </div>

      {/* Action Buttons */}
      <div className="space-y-2">
        <Link href={`/m/portfolio/${code}/trades`}>
          <Button className="w-full">
            调仓交易
          </Button>
        </Link>
      </div>

      {/* #595 §4.5：现金市值更新共享 Dialog */}
      <CashMarketValueUpdateDialog
        portfolioCode={code}
        open={isCashUpdateOpen}
        onOpenChange={setIsCashUpdateOpen}
      />
    </div>
  );
}
