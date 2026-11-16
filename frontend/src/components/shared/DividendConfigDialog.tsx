"use client";

import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DatePicker } from "@/components/ui/date-picker";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCreateDividendConfig, useDeleteDividendConfig, useDividendConfigList } from "@/hooks/useDividendConfig";
import { formatDate, parseDateOnly, toDateOnly } from "@/lib/utils";
import type { DividendConfigSource, DividendMethod } from "@/types/dividend-config";

const METHOD_LABELS: Record<DividendMethod, string> = {
  cash_dividend: "现金分红",
  reinvest_dividend: "分红再投资",
};
const SOURCE_LABELS: Record<DividendConfigSource, string> = {
  default: "系统默认",
  explicit: "显式配置",
};

interface DividendConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  portfolioCode: string;
  productCode: string;
  market: string;
  platformCode: string;
  productName?: string;
}

/** CN_OTC 持仓行配置管理入口（#887，SPEC §7.3，A14/R10）：当前生效方式+来源标签+历史+变更表单。
 *  仅 CN_OTC 行出现本入口；写侧 market 恒 CN_OTC 由后端守卫。default 行仅预选、不自动入账。 */
export default function DividendConfigDialog({
  open,
  onOpenChange,
  portfolioCode,
  productCode,
  market,
  platformCode,
  productName,
}: DividendConfigDialogProps) {
  const key = {
    portfolio_code: portfolioCode,
    product_code: productCode,
    platform_code: platformCode,
    market,
  };
  const { data: listData, isLoading } = useDividendConfigList(key, open);
  const createConfig = useCreateDividendConfig();
  const deleteConfig = useDeleteDividendConfig();
  const [method, setMethod] = useState<DividendMethod>("reinvest_dividend");
  const [effectiveFrom, setEffectiveFrom] = useState<string>(() => toDateOnly(new Date()) || "");
  const items = listData?.items ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>分红方式配置</DialogTitle>
          <DialogDescription>
            {productName || productCode} · {market} · {platformCode}（仅内地场外 CN_OTC 可配）
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>配置历史（生效日降序，首行为最新）</Label>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">加载中…</p>
            ) : items.length === 0 ? (
              <p className="text-sm text-muted-foreground" data-testid="dividend-config-empty">暂无配置</p>
            ) : (
              <ul className="space-y-1">
                {items.map((row) => (
                  <li
                    key={row.id}
                    className="flex items-center justify-between gap-2 text-sm"
                    data-testid="dividend-config-row"
                  >
                    <span className="min-w-0 truncate">
                      {formatDate(row.effective_from)} · {METHOD_LABELS[row.dividend_method]}{" "}
                      <Badge variant="outline">{SOURCE_LABELS[row.source]}</Badge>
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="shrink-0 text-destructive"
                      onClick={() => deleteConfig.mutate(row.id)}
                      disabled={deleteConfig.isPending}
                      data-testid={`dividend-config-delete-${row.id}`}
                    >
                      删除
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="space-y-2 border-t border-border pt-3">
            <Label htmlFor="dividend-config-method">新增/变更方式</Label>
            <Select value={method} onValueChange={(v) => setMethod(v as DividendMethod)}>
              <SelectTrigger id="dividend-config-method">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="cash_dividend">现金分红</SelectItem>
                <SelectItem value="reinvest_dividend">分红再投资</SelectItem>
              </SelectContent>
            </Select>
            <Label htmlFor="dividend-config-effective-from">生效起始日（含当日）</Label>
            <DatePicker
              id="dividend-config-effective-from"
              date={parseDateOnly(effectiveFrom)}
              onSelect={(d) => setEffectiveFrom(toDateOnly(d) || "")}
              placeholder="选择生效日"
            />
            <Button
              onClick={() => {
                if (effectiveFrom) {
                  createConfig.mutate({ ...key, dividend_method: method, effective_from: effectiveFrom });
                }
              }}
              disabled={!effectiveFrom || createConfig.isPending}
              data-testid="dividend-config-save"
            >
              保存配置
            </Button>
            <p className="text-xs text-muted-foreground">
              同键同生效日：系统默认行提升为显式；历史显式行不同方式将被拒绝。删除仅今日/未来生效行可删。
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}