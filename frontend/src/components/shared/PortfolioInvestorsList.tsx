"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import NameCodeCell from "@/components/shared/NameCodeCell";
import type { PortfolioInvestorItem } from "@/types/portfolio";
import {
  cn,
  formatCurrency,
  formatNav,
  formatNumber,
  formatSharesUnit,
  formatSignedCurrency,
  getNumberCellClass,
  getReturnColorClass,
  getSignedReturn,
  largestRemainderPercents,
} from "@/lib/utils";

interface PortfolioInvestorsListProps {
  investors?: PortfolioInvestorItem[];
  /** 移动端外层加横向滚动壳（同 SnapshotsContent/TradesContent 惯例） */
  variant?: "desktop" | "mobile";
}

/**
 * 占比列显示：percent 来自最大余数法（§4 toFixed(1) 是唯一许可例外）；
 * ratio 为 null（当日无市值快照行）时 `--` 兜底交回 formatNumber（§12:234
 * 禁 JSX 手写字面量），不渲染 largestRemainderPercents Σ≤0 时的全 0 兜底。
 */
function ratioText(ratio: number | null, percent: number | undefined): string {
  if (ratio == null) return formatNumber(null);
  return `${(percent ?? 0).toFixed(1)}%`;
}

// getNumberCellClass() 未含 #394 给 .number-cell 工具类的 nowrap，窄列下
// 「100,000.00 份」与 CJK 表头会折行/竖排（#355 同族），故本表显式补 nowrap
const NUM_CELL = cn(getNumberCellClass(), "whitespace-nowrap");

/**
 * 组合投资人列表（issue #99 抽出双端复用；#693 卡片改表格，暴露
 * investor_holding 已落库的派生列）：供 ?tab=investors 视图（双端）使用。
 * 占比用后端 ratio（同快照日 shares/total_shares，份额占比与市值占比数学
 * 等价故只此一列），展示层经 largestRemainderPercents 归一、加总恒 100.0%；
 * 派生列历史快照为 NULL 时直接进 format 函数兜底 `--`（不显示 0.00）。
 */
export default function PortfolioInvestorsList({
  investors,
  variant = "desktop",
}: PortfolioInvestorsListProps) {
  const nameMap = new Map<string, string>(
    (investors ?? []).map((i) => [i.investor_code, i.name])
  );
  const percents = largestRemainderPercents(
    (investors ?? []).map((i) => i.ratio ?? 0)
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>投资人列表</CardTitle>
        <CardDescription>组合中的投资人及份额、成本与持仓收益</CardDescription>
      </CardHeader>
      <CardContent>
        {!investors || investors.length === 0 ? (
          <div className="py-8 text-center text-muted-foreground">暂无投资人</div>
        ) : (
          <div className={variant === "mobile" ? "overflow-x-auto" : undefined}>
            <Table
              data-testid="portfolio-investors-table"
              className={variant === "mobile" ? "min-w-[900px]" : undefined}
            >
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap text-muted-foreground">投资人</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>份额</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>占比</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>市值</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>总成本</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>成本价</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>持仓收益</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>冻结</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {investors.map((investor, i) => {
                  const ret = getSignedReturn(investor.profit_percent);
                  return (
                    <TableRow key={investor.investor_code} data-testid="investor-row">
                      <TableCell>
                        <NameCodeCell code={investor.investor_code} nameMap={nameMap} />
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {formatSharesUnit(investor.shares)}
                      </TableCell>
                      <TableCell className={NUM_CELL} data-testid="investor-ratio">
                        {ratioText(investor.ratio, percents[i])}
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {formatCurrency(investor.market_value)}
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {formatCurrency(investor.total_cost)}
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {formatNav(investor.cost_per_share)}
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {/* 持仓收益 = market_value − total_cost（当前在手份额浮动盈亏）；
                            主行金额、副行收益率（#693，副行形态同 SnapshotsContent 涨跌列） */}
                        <div>
                          <span className={getReturnColorClass(investor.profit)}>
                            {formatSignedCurrency(investor.profit)}
                          </span>
                        </div>
                        <div className="text-xs">
                          <span className={ret.colorClass}>{ret.text}</span>
                        </div>
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {formatSharesUnit(investor.frozen_shares)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
