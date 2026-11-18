"use client";

import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DatePicker } from "@/components/ui/date-picker";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { Loader2 } from "lucide-react";
import { toDateOnly } from "@/lib/utils";
import { usePlatformList } from "@/hooks/usePlatform";
import { useCreateLedgerEntry, useUpdateLedgerEntry } from "@/hooks/useLedger";
import { useUIStore } from "@/stores/uiStore";
import type { Platform } from "@/types/platform";
import type { LedgerEntry } from "@/types/ledger";

interface LedgerEntryDialogProps {
  portfolioCode: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 传入则编辑该日市值（平台/日期为唯一键组成，锁定不改）；不传则新建 */
  entry?: LedgerEntry;
}

/**
 * 台账市值登记 Dialog（issue #905 PR3，D5）。
 * 新建：选平台 + 日期 + 市值 + 分类 + 备注（每平台每日一行，重复由后端唯一键拒绝）。
 * 编辑：平台/日期锁定，只改市值/分类/备注（改日期请删后重建）；写侧仅 active 放行（S3）。
 */
export default function LedgerEntryDialog({
  portfolioCode,
  open,
  onOpenChange,
  entry,
}: LedgerEntryDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  const { data: platformData } = usePlatformList({ page_size: 100 });
  const platforms: Platform[] = platformData?.items || [];
  const isEdit = !!entry;

  const [platform, setPlatform] = useState<string | null>(null);
  const [date, setDate] = useState<Date | undefined>(undefined);
  const [marketValue, setMarketValue] = useState("");
  const [assetKind, setAssetKind] = useState("non_cash");
  const [note, setNote] = useState("");

  const createEntry = useCreateLedgerEntry(portfolioCode);
  const updateEntry = useUpdateLedgerEntry(portfolioCode);
  const pending = createEntry.isPending || updateEntry.isPending;

  useEffect(() => {
    if (open) {
      setPlatform(entry?.platform_code ?? null);
      setDate(entry ? new Date(entry.value_date + "T00:00:00") : undefined);
      setMarketValue(entry ? String(entry.market_value) : "");
      setAssetKind(entry?.asset_kind ?? "non_cash");
      setNote(entry?.note ?? "");
    }
  }, [open, entry]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isEdit && !platform) {
      addToast({ type: "error", title: "输入错误", message: "请选择平台" });
      return;
    }
    if (!isEdit && !date) {
      addToast({ type: "error", title: "输入错误", message: "请选择日期" });
      return;
    }
    const mv = parseFloat(marketValue);
    if (marketValue === "" || !Number.isFinite(mv) || mv < 0) {
      addToast({ type: "error", title: "输入错误", message: "请输入有效的市值（≥0）" });
      return;
    }
    const onSuccess = () => onOpenChange(false);
    // S3（#905 PR3 L2）：错误 toast 只留 hook 一层（useLedger onError），Dialog 不再叠 onError，
    // 避免双层 onError 重复 toast。

    if (isEdit && entry) {
      updateEntry.mutate(
        { id: entry.id, data: { market_value: mv, asset_kind: assetKind, note: note || null } },
        { onSuccess },
      );
    } else {
      createEntry.mutate(
        {
          platform_code: platform!,
          value_date: toDateOnly(date),
          market_value: mv,
          asset_kind: assetKind,
          note: note || null,
        },
        { onSuccess },
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑市值记录" : "登记平台市值"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "修改该日市值/分类/备注（平台与日期不可改，改日期请删后重建）"
              : "按平台记录某日绝对市值，每平台每日一行"}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ledger-entry-platform">平台</Label>
            {isEdit ? (
              <Input id="ledger-entry-platform" value={entry?.platform_code ?? ""} disabled />
            ) : (
              <SearchablePlatformSelect
                platforms={platforms}
                value={platform}
                onChange={setPlatform}
                placeholder="请选择平台"
                id="ledger-entry-platform"
              />
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-entry-date">市值日期</Label>
            {isEdit ? (
              <Input id="ledger-entry-date" value={entry?.value_date ?? ""} disabled />
            ) : (
              <DatePicker
                id="ledger-entry-date"
                date={date}
                onSelect={setDate}
                placeholder="选择日期（任意自然日）"
              />
            )}
            {!isEdit && (
              <p className="text-xs text-muted-foreground">台账按自然日记录，不要求交易日</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-entry-value">市值（元）</Label>
            <Input
              id="ledger-entry-value"
              type="number"
              step="0.0001"
              min="0"
              value={marketValue}
              onChange={(e) => setMarketValue(e.target.value)}
              placeholder="请输入该平台当日市值"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-entry-kind">资产分类</Label>
            <Select value={assetKind} onValueChange={setAssetKind}>
              <SelectTrigger id="ledger-entry-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="non_cash">非现金类</SelectItem>
                <SelectItem value="cash">现金类</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">仅作展示分类，不并入净值型可用现金口径</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-entry-note">备注（可选）</Label>
            <Input
              id="ledger-entry-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="选填"
            />
          </div>

          <DialogFooter className="flex-col sm:flex-row gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} className="w-full sm:w-auto">
              取消
            </Button>
            <Button type="submit" disabled={pending} className="w-full sm:w-auto">
              {pending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {isEdit ? "保存" : "登记"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}