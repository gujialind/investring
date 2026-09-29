"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import MainLayout from "@/components/layout/MainLayout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCurrency, formatSharesUnit, formatNav, formatReturnRate, getReturnColorClass, toDateOnly } from "@/lib/utils";
import { Plus, ArrowLeft, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { ApiException } from "@/lib/api";
import { useUIStore } from "@/stores/uiStore";
import { usePositionList } from "@/hooks/usePosition";
import { useCreateTrade } from "@/hooks/useTrade";
import CashTransferDialog from "@/components/shared/dialogs/CashTransferDialog";
import CashTransferListDialog from "@/components/shared/dialogs/CashTransferListDialog";
import CashMarketValueUpdateDialog from "@/components/shared/dialogs/CashMarketValueUpdateDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { Position } from "@/types/position";
import type { TradeCreate } from "@/types/trade";

export default function PositionsPage() {
  const params = useParams();
  const code = params.code as string;
  const addToast = useUIStore((state) => state.addToast);

  const { data: positionsData, isLoading } = usePositionList(code);
  const positions: Position[] = positionsData?.items || [];

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [tradeType, setTradeType] = useState<"buy" | "sell">("buy");
  const [formData, setFormData] = useState({
    product_code: "",
    shares: "",
    amount: "",
    price: "",
  });

  // #595 §4.5：现金市值更新 Dialog 开关
  const [isCashUpdateOpen, setIsCashUpdateOpen] = useState(false);

  // 现金转移相关状态
  const [isTransferOpen, setIsTransferOpen] = useState(false);
  const [isTransferListOpen, setIsTransferListOpen] = useState(false);

  const totalMarketValue = positions.reduce((sum, p) => sum + (p.market_value || 0), 0);
  const totalCost = positions.reduce((sum, p) => sum + ((p.shares || 0) * (p.cost_price || 0)), 0);
  const totalProfitLoss = totalMarketValue - totalCost;

  // 创建交易走统一 hook（内部已正确 invalidate trades/positions/portfolios）
  const createTrade = useCreateTrade();
  // 命中 DUPLICATE_TRADE 时暂存待重试的交易，由确认框引导 allow_duplicate 重试
  const [duplicateTrade, setDuplicateTrade] = useState<TradeCreate | null>(null);

  const resetTradeForm = () => {
    setIsDialogOpen(false);
    setFormData({ product_code: "", shares: "", amount: "", price: "" });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.product_code || !formData.price) {
      addToast({
        type: "error",
        title: "表单校验失败",
        message: "请填写完整交易信息",
      });
      return;
    }

    const amount = tradeType === "buy" ? parseFloat(formData.amount || "0") : 0;
    const shares = tradeType === "sell" ? parseFloat(formData.shares || "0") : 0;

    if (tradeType === "buy" && (!amount || amount <= 0)) {
      addToast({
        type: "error",
        title: "表单校验失败",
        message: "买入金额必须大于0",
      });
      return;
    }

    if (tradeType === "sell" && (!shares || shares <= 0)) {
      addToast({
        type: "error",
        title: "表单校验失败",
        message: "卖出份额必须大于0",
      });
      return;
    }

    const tradeData: TradeCreate = {
      portfolio_code: code,
      product_code: formData.product_code,
      trade_type: tradeType,
      trade_date: toDateOnly(new Date()),
      price: parseFloat(formData.price),
      ...(tradeType === "buy"
        ? { amount }
        : { shares }),
      fee: 0,
    };

    createTrade.mutate(tradeData, {
      onSuccess: resetTradeForm,
      onError: (error: unknown) => {
        // 重复交易：弹确认框引导 allow_duplicate 重试（hook 层已抑制该错误码的 toast）
        if (error instanceof ApiException && error.code === "DUPLICATE_TRADE") {
          setDuplicateTrade(tradeData);
        }
      },
    });
  };

  return (
    <MainLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href={`/portfolio/${code}`}>
              <Button variant="ghost" size="sm">
                <ArrowLeft className="h-4 w-4" />
              </Button>
            </Link>
            <div>
              <h1 className="text-2xl font-semibold">持仓管理</h1>
              <p className="text-muted-foreground">组合代码: {code}</p>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setIsTransferListOpen(true)}>
              转移记录
            </Button>
            <Button variant="outline" onClick={() => setIsTransferOpen(true)}>
              现金转移
            </Button>
            <Button variant="outline" onClick={() => setIsCashUpdateOpen(true)}>
              <RefreshCw className="mr-2 h-4 w-4" />
              更新现金市值
            </Button>
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
              <DialogTrigger asChild>
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  调仓
                </Button>
              </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>调仓交易</DialogTitle>
                <DialogDescription>
                  提交买入或卖出交易
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={handleSubmit}>
                <div className="space-y-4 py-4">
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant={tradeType === "buy" ? "default" : "outline"}
                      onClick={() => setTradeType("buy")}
                      className="flex-1"
                    >
                      买入
                    </Button>
                    <Button
                      type="button"
                      variant={tradeType === "sell" ? "default" : "outline"}
                      onClick={() => setTradeType("sell")}
                      className="flex-1"
                    >
                      卖出
                    </Button>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="product_code">产品代码</Label>
                    <Input
                      id="product_code"
                      value={formData.product_code}
                      onChange={(e) => setFormData({ ...formData, product_code: e.target.value })}
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="price">价格</Label>
                    <Input
                      id="price"
                      type="number"
                      step="0.0001"
                      value={formData.price}
                      onChange={(e) => setFormData({ ...formData, price: e.target.value })}
                      required
                    />
                  </div>
                  {tradeType === "buy" ? (
                    <div className="space-y-2">
                      <Label htmlFor="amount">买入金额</Label>
                      <Input
                        id="amount"
                        type="number"
                        value={formData.amount}
                        onChange={(e) => setFormData({ ...formData, amount: e.target.value })}
                        required
                      />
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <Label htmlFor="shares">卖出份额</Label>
                      <Input
                        id="shares"
                        type="number"
                        value={formData.shares}
                        onChange={(e) => setFormData({ ...formData, shares: e.target.value })}
                        required
                      />
                    </div>
                  )}
                </div>
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={() => setIsDialogOpen(false)}>
                    取消
                  </Button>
                  <Button type="submit" disabled={createTrade.isPending}>
                    {createTrade.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    提交
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          {/* #595 §4.5：现金市值更新共享 Dialog */}
          <CashMarketValueUpdateDialog
            portfolioCode={code}
            open={isCashUpdateOpen}
            onOpenChange={setIsCashUpdateOpen}
          />

          {/* 现金转移对话框（步骤⑤起收敛为共享组件 CashTransferDialog） */}
          <CashTransferDialog
            portfolioCode={code}
            open={isTransferOpen}
            onOpenChange={setIsTransferOpen}
          />
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>持仓概览</CardTitle>
            <CardDescription>当前组合持仓及收益情况</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                加载中...
              </div>
            ) : (
              <>
                <div className="grid grid-cols-3 gap-4 mb-6">
                  <Card>
                    <CardContent className="pt-6">
                      <div className="text-2xl font-bold">{formatCurrency(totalMarketValue)}</div>
                      <p className="text-sm text-muted-foreground">总市值</p>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent className="pt-6">
                      <div className="text-2xl font-bold">{formatCurrency(totalCost)}</div>
                      <p className="text-sm text-muted-foreground">总成本</p>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent className="pt-6">
                      <div className={`text-2xl font-bold ${getReturnColorClass(totalProfitLoss)}`}>
                        {formatCurrency(totalProfitLoss)}
                      </div>
                      <p className="text-sm text-muted-foreground">总收益</p>
                    </CardContent>
                  </Card>
                </div>

                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>产品代码</TableHead>
                      <TableHead>产品名称</TableHead>
                      <TableHead>市场</TableHead>
                      <TableHead className="number-cell">持仓份额</TableHead>
                      <TableHead className="number-cell">成本价</TableHead>
                      <TableHead className="number-cell">当前价</TableHead>
                      <TableHead className="number-cell">市值</TableHead>
                      <TableHead className="number-cell">盈亏</TableHead>
                      <TableHead className="number-cell">收益率</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {positions.map((position) => (
                      <TableRow key={position.id}>
                        <TableCell className="font-medium">{position.product_code}</TableCell>
                        <TableCell>{position.product_name}</TableCell>
                        <TableCell>{position.market || "--"}</TableCell>
                        <TableCell className="number-cell">
                          {formatSharesUnit(position.shares)}
                        </TableCell>
                        <TableCell className="number-cell">
                          {formatNav(position.cost_price)}
                        </TableCell>
                        <TableCell className="number-cell">
                          {formatNav(position.unit_price)}
                        </TableCell>
                        <TableCell className="number-cell">
                          {formatCurrency(position.market_value)}
                        </TableCell>
                        <TableCell className="number-cell">
                          {position.profit_loss !== undefined && position.profit_loss !== null ? (
                            <span className={getReturnColorClass(position.profit_loss)}>
                              {formatCurrency(position.profit_loss)}
                            </span>
                          ) : (
                            "--"
                          )}
                        </TableCell>
                        <TableCell className="number-cell">
                          {position.profit_loss_percent !== undefined && position.profit_loss_percent !== null ? (
                            <span className={getReturnColorClass(position.profit_loss_percent)}>
                              {formatReturnRate(position.profit_loss_percent)}
                            </span>
                          ) : (
                            "--"
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {positions.length === 0 && (
                  <div className="text-center text-muted-foreground py-8">
                    暂无持仓数据
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 现金转移记录（含跨天转移确认到账） */}
      <CashTransferListDialog
        portfolioCode={code}
        open={isTransferListOpen}
        onOpenChange={setIsTransferListOpen}
      />

      {/* DUPLICATE_TRADE 确认重试 */}
      <AlertDialog open={!!duplicateTrade} onOpenChange={(open) => !open && setDuplicateTrade(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>检测到重复交易</AlertDialogTitle>
            <AlertDialogDescription>
              存在同组合/产品/方向/交易日且金额或份额相同的交易，是否仍要提交？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (duplicateTrade) {
                  createTrade.mutate(
                    { ...duplicateTrade, allow_duplicate: true },
                    { onSuccess: resetTradeForm }
                  );
                }
                setDuplicateTrade(null);
              }}
            >
              仍要提交
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </MainLayout>
  );
}