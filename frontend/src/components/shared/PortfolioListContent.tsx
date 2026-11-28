"use client";

import PageHeader from "@/components/shared/PageHeader";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Eye, Users, Loader2, Power, PowerOff, Trash2 } from "lucide-react";
import { formatCurrency, formatReturnRate, getReturnColorClass, getStatusBadgeVariant } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  usePortfolioList,
  useCreatePortfolio,
  useClosePortfolio,
  useActivatePortfolio,
  useDeletePortfolio,
} from "@/hooks/usePortfolio";
import { useRoleCheck } from "@/hooks/useAuth";
import { useInvestorList } from "@/hooks/useInvestor";
import { useUIStore } from "@/stores/uiStore";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import ClosePortfolioDialog from "@/components/shared/dialogs/ClosePortfolioDialog";
import DeletePortfolioDialog from "@/components/shared/dialogs/DeletePortfolioDialog";
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

interface PortfolioListContentProps {
  /** 链接前缀：桌面 "/portfolio"，移动 "/m/portfolio" */
  basePath: string;
  /** 桌面: 卡片 3 列网格；移动: 单列卡片 */
  variant?: "desktop" | "mobile";
}

/**
 * 组合列表页内容（桌面/移动共用）。
 * 抽离自原 app/portfolio/page.tsx，两端仅 basePath 与 variant 不同。
 * 关闭组合用 ClosePortfolioDialog（惰性查询持仓/投资人，issue #99），
 * 删除空壳（draft/closed）用 DeletePortfolioDialog（三处共用一份确认文案，issue #820），
 * 重新激活仍是本页内联的 AlertDialog 确认。
 */
export default function PortfolioListContent({ basePath, variant = "desktop" }: PortfolioListContentProps) {
  const router = useRouter();
  const { data, isLoading } = usePortfolioList({ page_size: 100 });
  const createPortfolio = useCreatePortfolio();
  const closePortfolio = useClosePortfolio();
  const activatePortfolio = useActivatePortfolio();
  const deletePortfolio = useDeletePortfolio();
  const { isAdmin } = useRoleCheck();
  const addToast = useUIStore((state) => state.addToast);
  const { data: investorsData } = useInvestorList({ page_size: 100 });
  const investors = investorsData?.items || [];

  const portfolios = data?.items || [];

  const [filter, setFilter] = useState<string>("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [formData, setFormData] = useState({ code: "", name: "", description: "", kind: "nav", owner_investor_code: "" });
  // 关闭组合：ClosePortfolioDialog 目标；重新激活 / 删除：AlertDialog 目标
  const [closeTarget, setCloseTarget] = useState<string | null>(null);
  const [activateTarget, setActivateTarget] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const filteredPortfolios = portfolios.filter(
    (p) => (filter === "all" || p.status === filter) && (kindFilter === "all" || p.kind === kindFilter)
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const isLedger = formData.kind === "ledger";
    if (isLedger && !formData.owner_investor_code) {
      addToast({ type: "error", title: "输入错误", message: "台账型组合须选择归属投资人" });
      return;
    }
    const payload = isLedger
      ? { code: formData.code, name: formData.name, description: formData.description, kind: "ledger", owner_investor_code: formData.owner_investor_code }
      : { code: formData.code, name: formData.name, description: formData.description };
    createPortfolio.mutate(payload, {
      onSuccess: (created) => {
        setIsDialogOpen(false);
        setFormData({ code: "", name: "", description: "", kind: "nav", owner_investor_code: "" });
        router.push(`${basePath}/${created.code}`);
      },
    });
  };

  const handleActivate = () => {
    if (!activateTarget) return;
    activatePortfolio.mutate(activateTarget);
    setActivateTarget(null);
  };

  // 确认弹窗要点名 name + code（#820 口径 Q4）：删除不可恢复，只给 code 不够认对象
  const deleteTargetPortfolio = portfolios.find((p) => p.code === deleteTarget);

  if (isLoading) return <LoadingState />;

  const gridCls =
    variant === "mobile" ? "grid grid-cols-1 gap-3" : "grid gap-4 md:grid-cols-2 lg:grid-cols-3";

  return (
    <div className="space-y-6">
      <PageHeader
        variant={variant}
        title="组合管理"
        meta="管理投资组合和查看收益"
        actions={isAdmin && (
          <>
            {/* 投资人管理语境入口（#878）：权重降为 ghost 链接档（#904），入口语义不变 */}
            <Button asChild variant="ghost" className="text-muted-foreground">
              <Link href={variant === "mobile" ? "/m/investors" : "/investors"}>
                <Users className="mr-2 h-4 w-4" />
                投资人管理
              </Link>
            </Button>
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                创建组合
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>创建组合</DialogTitle>
                <DialogDescription>创建新的投资组合</DialogDescription>
              </DialogHeader>
              <form onSubmit={handleSubmit}>
                <div className="space-y-4 py-4">
                  <div className="space-y-2">
                    <Label htmlFor="code">组合代码</Label>
                    <Input
                      id="code"
                      value={formData.code}
                      onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                      placeholder="如: PORT001"
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="name">组合名称</Label>
                    <Input
                      id="name"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                      placeholder="如: 稳健增长组合"
                      required
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="description">描述</Label>
                    <Input
                      id="description"
                      value={formData.description}
                      onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                      placeholder="可选"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="kind">组合类型</Label>
                    <Select value={formData.kind} onValueChange={(v) => setFormData({ ...formData, kind: v })}>
                      <SelectTrigger id="kind" data-testid="portfolio-create-kind">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="nav">净值型</SelectItem>
                        <SelectItem value="ledger">平台市值台账型</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">类型创建后不可改；台账型按平台记市值与本金进出，不算净值</p>
                  </div>
                  {formData.kind === "ledger" && (
                    <div className="space-y-2">
                      <Label htmlFor="owner">归属投资人</Label>
                      <Select
                        value={formData.owner_investor_code}
                        onValueChange={(v) => setFormData({ ...formData, owner_investor_code: v })}
                      >
                        <SelectTrigger id="owner" data-testid="portfolio-create-owner">
                          <SelectValue placeholder="请选择归属投资人" />
                        </SelectTrigger>
                        <SelectContent>
                          {investors.map((inv) => (
                            <SelectItem key={inv.code} value={inv.code}>{inv.name}（{inv.code}）</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">台账型组合归属单一投资人（必填）</p>
                    </div>
                  )}
                </div>
                <DialogFooter>
                  <Button type="submit" disabled={createPortfolio.isPending}>
                    {createPortfolio.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    创建组合
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
          </>
        )}
      />

      <div className="flex items-center gap-4">
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="筛选状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="active">活跃</SelectItem>
            <SelectItem value="draft">草稿</SelectItem>
            <SelectItem value="closed">已关闭</SelectItem>
          </SelectContent>
        </Select>
        <Select value={kindFilter} onValueChange={setKindFilter}>
          <SelectTrigger className="w-[180px]" data-testid="portfolio-kind-filter">
            <SelectValue placeholder="筛选类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            <SelectItem value="nav">净值型</SelectItem>
            <SelectItem value="ledger">平台市值台账型</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className={gridCls}>
        {filteredPortfolios.map((portfolio) => (
          <Card key={portfolio.code}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>{portfolio.name}</CardTitle>
                <Badge variant={getStatusBadgeVariant(portfolio.status)}>
                  {portfolio.status === "active" ? "活跃" : portfolio.status === "draft" ? "草稿" : "已关闭"}
                </Badge>
              </div>
              <CardDescription>{portfolio.code}{portfolio.kind === "ledger" ? " · 平台市值台账型" : ""}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {portfolio.status === "active" && portfolio.kind === "ledger" && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">当前市值</span>
                      <span className="font-medium">{formatCurrency(portfolio.total_value ?? 0)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">累计收益</span>
                      <span className={`font-medium ${getReturnColorClass(portfolio.total_profit ?? 0)}`}>
                        {formatCurrency(portfolio.total_profit ?? 0)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">归属投资人</span>
                      <span className="font-medium">{portfolio.owner_investor_code || "--"}</span>
                    </div>
                  </>
                )}
                {portfolio.status === "active" && portfolio.kind !== "ledger" && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">总资产</span>
                      <span className="font-medium">{formatCurrency(portfolio.total_value || 0)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">累计收益</span>
                      <span className={`font-medium ${getReturnColorClass(portfolio.cumulative_return || 0)}`}>
                        {formatReturnRate(portfolio.cumulative_return || 0)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">投资人</span>
                      <div className="flex items-center gap-1">
                        <Users className="h-4 w-4 text-muted-foreground" />
                        <span className="font-medium">{portfolio.investor_count || 0}</span>
                      </div>
                    </div>
                  </>
                )}
                {portfolio.status === "draft" && (
                  <div className="text-sm text-muted-foreground py-2">
                    {portfolio.kind === "ledger"
                      ? "组合尚未激活，请先激活后按平台登记台账"
                      : "组合尚未激活，请执行首次申购以启动组合"}
                  </div>
                )}
                <div className="flex gap-2 pt-2">
                  <Link href={`${basePath}/${portfolio.code}`} className="flex-1">
                    <Button variant="outline" className="w-full">
                      <Eye className="mr-2 h-4 w-4" />
                      详情
                    </Button>
                  </Link>
                  {/* 投资人入口：详情页 ?tab=investors 视图（issue #99）；台账型单归属无投资人份额视图 */}
                  {portfolio.kind !== "ledger" && (
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => router.push(`${basePath}/${portfolio.code}?tab=investors`)}
                      title="投资人"
                    >
                      <Users className="h-4 w-4" />
                    </Button>
                  )}
                  {isAdmin && portfolio.status === "active" && (
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => setCloseTarget(portfolio.code)}
                      disabled={closePortfolio.isPending}
                      title="关闭组合"
                    >
                      <PowerOff className="h-4 w-4 text-destructive" />
                    </Button>
                  )}
                  {isAdmin && portfolio.status === "closed" && (
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => setActivateTarget(portfolio.code)}
                      disabled={activatePortfolio.isPending}
                      title="重新激活"
                    >
                      <Power className="h-4 w-4 text-success" />
                    </Button>
                  )}
                  {/* 空壳（draft/closed）才可删（#820 扩围）；是否真空壳由后端 409 判定，
                      入口不做本地预判。active 不出现该按钮（走生命周期而非删除）。
                      E2E 锚点：卡片内按钮的可及名同为「删除组合」，页面上会有多张可删卡，
                      故按 code 给 testid（frontend/e2e/AGENTS.md 的定位器契约：需要锚点就在
                      组件侧补 data-testid，不用类名/xpath 祖先定位）。 */}
                  {isAdmin && (portfolio.status === "draft" || portfolio.status === "closed") && (
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => setDeleteTarget(portfolio.code)}
                      disabled={deletePortfolio.isPending}
                      title="删除组合"
                      data-testid={`portfolio-delete-${portfolio.code}`}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {filteredPortfolios.length === 0 && <EmptyState message="暂无符合条件的组合" />}

      {/* 关闭组合：惰性查询弹窗（issue #99） */}
      <ClosePortfolioDialog
        open={!!closeTarget}
        onOpenChange={(open) => !open && setCloseTarget(null)}
        portfolioCode={closeTarget ?? ""}
        isPending={closePortfolio.isPending}
        onConfirm={() => {
          if (!closeTarget) return;
          closePortfolio.mutate(closeTarget, {
            onSettled: () => setCloseTarget(null),
          });
        }}
      />

      {/* 重新激活确认 */}
      <AlertDialog open={!!activateTarget} onOpenChange={(open) => !open && setActivateTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重新激活组合</AlertDialogTitle>
            <AlertDialogDescription>确定要重新激活该组合吗？</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleActivate();
              }}
              className="bg-destructive-solid text-white hover:bg-destructive-solid/90"
            >
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* 删除空壳确认（issue #820 扩围）：点名 name + code，说明不可恢复与 code 可复用 */}
      <DeletePortfolioDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        portfolioCode={deleteTarget ?? ""}
        portfolioName={deleteTargetPortfolio?.name}
        isPending={deletePortfolio.isPending}
        onConfirm={() => {
          if (!deleteTarget) return;
          deletePortfolio.mutate(deleteTarget, {
            onSettled: () => setDeleteTarget(null),
          });
        }}
      />
    </div>
  );
}
