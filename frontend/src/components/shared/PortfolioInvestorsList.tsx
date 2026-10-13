"use client";

import { Badge } from "@/components/ui/badge";
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
 * investor_holding 已落库的派生列；#694 加累计收益*列与清仓行）：
 * 供 ?tab=investors 视图（双端）使用。
 * 占比用后端 ratio（同快照日 shares/total_shares，份额占比与市值占比数学
 * 等价故只此一列），展示层经 largestRemainderPercents 归一、加总恒 100.0%；
 * 派生列历史快照为 NULL 时直接进 format 函数兜底 `--`（不显示 0.00）。
 * 清仓行（is_liquidated）份额/市值/占比显示真 0、派生列 `--`、累计收益保留。
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
            {/* min-w 是移动横滚壳下限（#693 八列形态定）；实际宽度由表格内容自然
                宽度撑开（#694 九列实测 918），加列后目检见挤压再同步上调 */}
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
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>累计收益*</TableHead>
                  <TableHead className={cn("text-muted-foreground", NUM_CELL)}>冻结</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {investors.map((investor, i) => {
                  const ret = getSignedReturn(investor.profit_percent);
                  return (
                    <TableRow
                      key={investor.investor_code}
                      data-testid={investor.is_liquidated ? "investor-row-liquidated" : "investor-row"}
                    >
                      <TableCell>
                        {/* flex-wrap：窄列下徽章换到名称下一行，而非撑宽首列挤垮数字列 */}
                        <div className="flex flex-wrap items-center gap-2">
                          <NameCodeCell code={investor.investor_code} nameMap={nameMap} />
                          {investor.is_liquidated && <Badge variant="secondary">已清仓</Badge>}
                        </div>
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
                      <TableCell className={NUM_CELL} data-testid="investor-cumulative">
                        {/* #694 方案 A 全历史净流量口径；与持仓收益并列、* 注记见表下 */}
                        <span className={getReturnColorClass(investor.cumulative_profit)}>
                          {formatSignedCurrency(investor.cumulative_profit)}
                        </span>
                      </TableCell>
                      <TableCell className={NUM_CELL}>
                        {formatSharesUnit(investor.frozen_shares)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            {/* 口径注记有数据时恒渲染（空态走「暂无投资人」分支不渲染；形态同
                ProductDetailContent 累计收益* 注记，visual-spec §20） */}
            <p className="mt-2 text-xs text-warning" data-testid="investor-cumulative-note">
              *累计收益 = 当前市值 + 已确认赎回金额 − 已确认申购金额；产品分红与调整
              未归属到投资人，与「持仓收益」（在手份额浮动盈亏）口径不同
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
