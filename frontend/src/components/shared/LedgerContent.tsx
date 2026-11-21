"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Plus, Pencil, Trash2, AlertTriangle } from "lucide-react";
import QueryErrorState from "@/components/shared/QueryErrorState";
import EmptyState from "@/components/shared/EmptyState";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import LedgerEntryDialog from "@/components/shared/dialogs/LedgerEntryDialog";
import LedgerFlowDialog from "@/components/shared/dialogs/LedgerFlowDialog";
import LedgerPerformance from "@/components/shared/LedgerPerformance";
import {
  useLedgerEntries,
  useLedgerFlows,
  useLedgerTotals,
  useDeleteLedgerEntry,
  useDeleteLedgerFlow,
} from "@/hooks/useLedger";
import { usePortfolioPerformance } from "@/hooks/usePortfolio";
import { useRoleCheck } from "@/hooks/useAuth";
import { usePlatformList } from "@/hooks/usePlatform";
import { formatAmount4, formatCurrency, getNumberCellClass } from "@/lib/utils";
import type { Portfolio } from "@/types/portfolio";
import type { LedgerEntry, LedgerFlow } from "@/types/ledger";

interface LedgerContentProps {
  portfolio: Portfolio;
  variant?: "desktop" | "mobile";
}

function StatBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 border rounded-lg">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-1 text-xl font-bold ${getNumberCellClass()}`}>{value}</div>
    </div>
  );
}

/**
 * 平台市值台账型组合视图（issue #905 PR3）。
 *
 * 按平台列示当前市值、登记市值（可改某日并留痕）、登记本金进出（同日多笔）；
 * 收益=当前市值−净入金（step-forward，后端派生）；绩效口径A（LedgerPerformance）。
 * - S3：draft 不可预录——未激活只给激活引导，不渲染登记入口；写侧仅 active 放行。
 * - S8：写入口按 isAdmin 条件渲染（viewer 只见读侧，不复刻 #642 见按钮缺口）；后端 403 兜底。
 * - S4：净入金为负时 totals.warnings 如实展示，不静默。
 * - 读侧失败态走 QueryErrorState（isError 优先于空态，失败≠空态）。
 */
export default function LedgerContent({ portfolio, variant = "desktop" }: LedgerContentProps) {
  const code = portfolio.code;
  const isDraft = portfolio.status === "draft";
  const isActive = portfolio.status === "active";
  const { isAdmin } = useRoleCheck();
  const canWrite = isAdmin && isActive; // S3 + S8

  const { data: platformsData } = usePlatformList({ page_size: 100 });
  const platformName = (c: string) =>
    platformsData?.items?.find((p) => p.code === c)?.name ?? c;

  const totals = useLedgerTotals(code);
  const entries = useLedgerEntries(code);
  const flows = useLedgerFlows(code);
  const performanceQuery = usePortfolioPerformance(code, !isDraft);
  const deleteEntry = useDeleteLedgerEntry(code);
  const deleteFlow = useDeleteLedgerFlow(code);

  const [entryDialogOpen, setEntryDialogOpen] = useState(false);
  const [editingEntry, setEditingEntry] = useState<LedgerEntry | undefined>(undefined);
  const [flowDialogOpen, setFlowDialogOpen] = useState(false);
  const [editingFlow, setEditingFlow] = useState<LedgerFlow | undefined>(undefined);
  const [pendingDeleteEntry, setPendingDeleteEntry] = useState<LedgerEntry | null>(null);
  const [pendingDeleteFlow, setPendingDeleteFlow] = useState<LedgerFlow | null>(null);

  const gridCls = variant === "mobile" ? "grid grid-cols-1 gap-2" : "grid grid-cols-3 gap-3";
  const t = totals.data;

  const openAddEntry = () => { setEditingEntry(undefined); setEntryDialogOpen(true); };
  const openEditEntry = (e: LedgerEntry) => { setEditingEntry(e); setEntryDialogOpen(true); };
  const openAddFlow = () => { setEditingFlow(undefined); setFlowDialogOpen(true); };
  const openEditFlow = (f: LedgerFlow) => { setEditingFlow(f); setFlowDialogOpen(true); };

  return (
    <div className="space-y-4">
      {/* draft 激活引导（S3：不可预录；绝不出现「首次申购…初始净值 1.0000」文案，断言1）。
          激活动作在页头 PortfolioActionButtons（kind 感知），此处仅引导。 */}
      {isDraft && (
        <Alert>
          <AlertDescription>
            组合尚未激活。平台市值台账型需先手动激活（页头「激活组合」），再按平台登记市值与本金进出（无需申购）。
          </AlertDescription>
        </Alert>
      )}

      {/* 收益汇总（读侧 draft/active/closed 均可读；draft 各值 None → "--"） */}
      <Card>
        <CardHeader>
          <CardTitle>台账汇总</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {totals.isError ? (
            <QueryErrorState error={totals.error} onRetry={() => totals.refetch()} className="mt-3" />
          ) : (
            <>
              <div className={gridCls}>
                <StatBox label="当前市值" value={formatAmount4(t?.total_value)} />
                <StatBox label="净入金" value={formatCurrency(t?.net_inflow)} />
                <StatBox label="累计收益" value={formatAmount4(t?.total_profit)} />
              </div>
              {/* S4：净入金为负告警，如实展示不静默 */}
              {t?.warnings && t.warnings.length > 0 && (
                <Alert className="border-warning/30 bg-warning-soft text-warning-foreground">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    <ul className="list-disc pl-4 text-sm">
                      {t.warnings.map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
              {isDraft && (
                <p className="text-xs text-muted-foreground">激活后开始登记，汇总将显示市值与收益。</p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* 平台市值台账（entry） */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>平台市值</CardTitle>
          {canWrite && (
            <Button size="sm" data-testid="ledger-entry-add" onClick={openAddEntry}>
              <Plus className="mr-2 h-4 w-4" />
              登记市值
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {entries.isError ? (
            <QueryErrorState error={entries.error} onRetry={() => entries.refetch()} className="mt-3" />
          ) : (entries.data?.length ?? 0) === 0 ? (
            <EmptyState message="暂无市值记录" description={canWrite ? "点击「登记市值」按平台记录某日市值" : "激活后由管理员登记"} />
          ) : (
            <div className={variant === "mobile" ? "overflow-x-auto" : undefined}>
            <Table className={variant === "mobile" ? "min-w-[640px]" : undefined}>
              <TableHeader>
                <TableRow>
                  <TableHead>平台</TableHead>
                  <TableHead>日期</TableHead>
                  <TableHead className="text-right">市值</TableHead>
                  <TableHead>分类</TableHead>
                  <TableHead>备注</TableHead>
                  {canWrite && <TableHead className="text-right">操作</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.data?.map((e) => (
                  <TableRow key={e.id} data-testid={`ledger-entry-row-${e.id}`}>
                    <TableCell>{platformName(e.platform_code)}</TableCell>
                    <TableCell>{e.value_date}</TableCell>
                    <TableCell className={getNumberCellClass()}>{formatAmount4(e.market_value)}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{e.asset_kind === "cash" ? "现金类" : "非现金类"}</Badge>
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate text-muted-foreground">{e.note || "--"}</TableCell>
                    {canWrite && (
                      <TableCell className="text-right">
                        <Button variant="ghost" size="sm" data-testid={`ledger-entry-edit-${e.id}`} onClick={() => openEditEntry(e)}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" data-testid={`ledger-entry-delete-${e.id}`} onClick={() => setPendingDeleteEntry(e)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 本金进出（flow） */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>本金进出</CardTitle>
          {canWrite && (
            <Button size="sm" data-testid="ledger-flow-add" onClick={openAddFlow}>
              <Plus className="mr-2 h-4 w-4" />
              登记进出
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {flows.isError ? (
            <QueryErrorState error={flows.error} onRetry={() => flows.refetch()} className="mt-3" />
          ) : (flows.data?.length ?? 0) === 0 ? (
            <EmptyState message="暂无进出记录" description={canWrite ? "点击「登记进出」记录本金流入/流出" : "激活后由管理员登记"} />
          ) : (
            <div className={variant === "mobile" ? "overflow-x-auto" : undefined}>
            <Table className={variant === "mobile" ? "min-w-[640px]" : undefined}>
              <TableHeader>
                <TableRow>
                  <TableHead>平台</TableHead>
                  <TableHead>日期</TableHead>
                  <TableHead>方向</TableHead>
                  <TableHead className="text-right">金额</TableHead>
                  <TableHead>备注</TableHead>
                  {canWrite && <TableHead className="text-right">操作</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {flows.data?.map((f) => (
                  <TableRow key={f.id} data-testid={`ledger-flow-row-${f.id}`}>
                    <TableCell>{platformName(f.platform_code)}</TableCell>
                    <TableCell>{f.flow_date}</TableCell>
                    <TableCell>
                      <Badge variant={f.direction === "inflow" ? "default" : "secondary"}>
                        {f.direction === "inflow" ? "流入" : "流出"}
                      </Badge>
                    </TableCell>
                    <TableCell className={getNumberCellClass()}>{formatCurrency(f.amount)}</TableCell>
                    <TableCell className="max-w-[200px] truncate text-muted-foreground">{f.note || "--"}</TableCell>
                    {canWrite && (
                      <TableCell className="text-right">
                        <Button variant="ghost" size="sm" data-testid={`ledger-flow-edit-${f.id}`} onClick={() => openEditFlow(f)}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" data-testid={`ledger-flow-delete-${f.id}`} onClick={() => setPendingDeleteFlow(f)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 绩效口径A（draft 不请求/不渲染）。B1（#905 PR3 L2）：请求失败走 QueryErrorState
          （失败≠空态），不把失败渲染成七个 "--" 空态。 */}
      {!isDraft &&
        (performanceQuery.isError ? (
          <QueryErrorState
            error={performanceQuery.error}
            onRetry={() => performanceQuery.refetch()}
          />
        ) : (
          <LedgerPerformance data={performanceQuery.data} variant={variant} />
        ))}

      {/* 登记/编辑 Dialog */}
      <LedgerEntryDialog
        portfolioCode={code}
        open={entryDialogOpen}
        onOpenChange={setEntryDialogOpen}
        entry={editingEntry}
      />
      <LedgerFlowDialog
        portfolioCode={code}
        open={flowDialogOpen}
        onOpenChange={setFlowDialogOpen}
        flow={editingFlow}
      />

      {/* 删除二次确认（危险操作，visual-spec §13） */}
      <ConfirmDialog
        open={!!pendingDeleteEntry}
        onOpenChange={(o) => !o && setPendingDeleteEntry(null)}
        title="删除市值记录"
        description={`将删除 ${pendingDeleteEntry?.value_date} ${platformName(pendingDeleteEntry?.platform_code ?? "")} 的市值记录，删除后该日缺失、读侧回退前值。`}
        confirmText="确认删除"
        onConfirm={() => {
          if (pendingDeleteEntry) {
            deleteEntry.mutate(pendingDeleteEntry.id, { onSettled: () => setPendingDeleteEntry(null) });
          }
        }}
      />
      <ConfirmDialog
        open={!!pendingDeleteFlow}
        onOpenChange={(o) => !o && setPendingDeleteFlow(null)}
        title="删除进出记录"
        description={`将删除 ${pendingDeleteFlow?.flow_date} ${platformName(pendingDeleteFlow?.platform_code ?? "")} 的本金进出记录。`}
        confirmText="确认删除"
        onConfirm={() => {
          if (pendingDeleteFlow) {
            deleteFlow.mutate(pendingDeleteFlow.id, { onSettled: () => setPendingDeleteFlow(null) });
          }
        }}
      />
    </div>
  );
}