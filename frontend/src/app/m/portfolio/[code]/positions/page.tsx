"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatCurrency, getReturnColorClass, toDateOnly } from "@/lib/utils";
import { ArrowLeft, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { platformApi } from "@/lib/api";
import { useUIStore } from "@/stores/uiStore";
import { usePositionList, useUpdateCashPosition } from "@/hooks/usePosition";
import PositionCard from "@/components/shared/PositionCard";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { DatePicker } from "@/components/ui/date-picker";
import type { Position } from "@/types/position";
import type { Platform } from "@/types/platform";

export default function MobilePositionsPage() {
  const params = useParams();
  const code = params.code as string;
  const addToast = useUIStore((state) => state.addToast);

  const { data: positionsData, isLoading } = usePositionList(code);
  const positions: Position[] = positionsData?.items || [];

  // 非净值资产更新相关状态
  const [isCashUpdateOpen, setIsCashUpdateOpen] = useState(false);
  const [cashAmount, setCashAmount] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState("");
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(undefined);

  // 获取平台列表
  const { data: platformsData } = useQuery({
    queryKey: ["platforms"],
    queryFn: () => platformApi.list({ page_size: 100 }),
  });
  const platforms: Platform[] = platformsData?.items || [];

  const totalMarketValue = positions.reduce((sum, p) => sum + (p.market_value || 0), 0);
  const totalCost = positions.reduce((sum, p) => sum + ((p.shares || 0) * (p.cost_price || 0)), 0);
  const totalProfitLoss = totalMarketValue - totalCost;

  // 更新非净值资产走统一 hook（与 PC 端持仓页共用，避免双端逻辑漂移）
  const updateCashPosition = useUpdateCashPosition(code);

  const handleCashUpdateSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!cashAmount || parseFloat(cashAmount) < 0) {
      addToast({
        type: "error",
        title: "输入错误",
        message: "请输入有效的金额",
      });
      return;
    }
    
    if (!selectedPlatform) {
      addToast({
        type: "error",
        title: "输入错误",
        message: "请选择平台",
      });
      return;
    }
    
    updateCashPosition.mutate(
      {
        amount: parseFloat(cashAmount),
        platformCode: selectedPlatform,
        updateDate: selectedDate ? toDateOnly(selectedDate) : undefined,
      },
      {
        onSuccess: () => {
          setIsCashUpdateOpen(false);
          setCashAmount("");
          setSelectedPlatform("");
          setSelectedDate(undefined);
        },
      }
    );
  };

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

      {/* 非净值资产更新对话框 */}
      <Dialog open={isCashUpdateOpen} onOpenChange={setIsCashUpdateOpen} modal={false}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>更新非净值资产</DialogTitle>
            <DialogDescription>
              更新现金等非净值型资产的当前金额
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCashUpdateSubmit}>
            <div className="space-y-4 py-4">
              {/* 平台选择 */}
              <div className="space-y-2">
                <Label htmlFor="platform">平台</Label>
                <SearchablePlatformSelect
                  platforms={platforms}
                  value={selectedPlatform || null}
                  onChange={(v) => setSelectedPlatform(v ?? "")}
                  placeholder="请选择平台"
                  id="platform"
                />
              </div>

              {/* 日期选择 */}
              <div className="space-y-2">
                <Label>更新日期（可选）</Label>
                <DatePicker
                  date={selectedDate}
                  onSelect={setSelectedDate}
                  placeholder="选择日期"
                />
                <p className="text-xs text-muted-foreground">
                  提示：只能选择交易日，非交易日将无法更新
                </p>
              </div>

              {/* 金额输入 */}
              <div className="space-y-2">
                <Label htmlFor="cash_amount">当前金额（元）</Label>
                <Input
                  id="cash_amount"
                  type="number"
                  step="0.01"
                  min="0"
                  value={cashAmount}
                  onChange={(e) => setCashAmount(e.target.value)}
                  placeholder="请输入当前现金金额"
                  required
                />
              </div>
            </div>
            <DialogFooter className="flex-col sm:flex-row gap-2">
              <Button 
                type="button" 
                variant="outline" 
                onClick={() => setIsCashUpdateOpen(false)}
                className="w-full sm:w-auto"
              >
                取消
              </Button>
              <Button 
                type="submit" 
                disabled={updateCashPosition.isPending}
                className="w-full sm:w-auto"
              >
                {updateCashPosition.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                确认更新
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
