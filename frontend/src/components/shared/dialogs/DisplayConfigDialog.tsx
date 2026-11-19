"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Loader2 } from "lucide-react";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import { useUpdatePortfolio } from "@/hooks/usePortfolio";
import {
  DEFAULT_SUB_DIMS_BY_CLASS,
  DIMENSION_LABELS,
  RULE_DIMENSIONS,
  parseDisplaySubset,
  type SubDimension,
} from "@/lib/dimensions";

interface DisplayConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  portfolioCode: string;
  /** 组合当前 display_config（null/缺省 = 未配置；值为逗号分隔维度子集串，#915） */
  currentConfig?: Record<string, string> | null;
}

/**
 * 持仓明细二级组合键维度子集配置弹窗（issue #144 创建、#915 单选改多选，双端共用）。
 * 枚举维度规则矩阵中有行的全部大类（不限当前持仓，支持预配置；
 * 现金大类无规则行天然不出现），每大类列出全部非禁止维度（required/optional
 * 均可）供勾选。保存仅提交显式覆盖项（勾选子集按固定全序拼逗号串）；
 * 清空某大类全部勾选（或点「恢复默认」）= 提交时省略该键 = 走默认子集，
 * 不是平铺（#915 Q8 不支持强制平铺）；全部大类默认时传 null 清空配置。
 *
 * 字典查询与详情页同 key（"asset_class"，顶层 dimension_rules 不受
 * 维度过滤影响），复用页面缓存且被页面 loading 闸门覆盖，打开弹窗时
 * 字典必然就绪；初始化 effect 仍依赖字典数据——晚到（缓存未命中）时
 * 数据到达后重跑初始化，避免 selections 停留在空状态导致保存误清空配置。
 */
export default function DisplayConfigDialog({
  open,
  onOpenChange,
  portfolioCode,
  currentConfig,
}: DisplayConfigDialogProps) {
  const { data: dictData } = useAssetClassifications("asset_class");
  const updatePortfolio = useUpdatePortfolio(portfolioCode);

  // 可配置大类：字典中 asset_class 维度值且在规则矩阵中有行（sort_order 排序）；
  // useMemo 稳定引用，供下方初始化 effect 依赖
  const configurableClasses = useMemo(
    () =>
      (dictData?.items ?? [])
        .filter(
          (i) =>
            i.dimension === "asset_class" &&
            Object.keys(dictData?.dimension_rules?.[i.code] ?? {}).length > 0
        )
        .sort((a, b) => a.sort_order - b.sort_order),
    [dictData]
  );

  // 各大类当前勾选子集（恒按全序）：打开时从既有配置回填；空数组 = 默认。
  // 依赖 configurableClasses/currentConfig——字典晚到时数据到达后重跑初始化
  const [selections, setSelections] = useState<Record<string, SubDimension[]>>({});
  useEffect(() => {
    if (!open) return;
    const init: Record<string, SubDimension[]> = {};
    for (const cls of configurableClasses) {
      init[cls.code] = parseDisplaySubset(currentConfig?.[cls.code]);
    }
    setSelections(init);
  }, [open, configurableClasses, currentConfig]);

  const toggleDim = (classCode: string, dim: SubDimension, checked: boolean) => {
    setSelections((prev) => {
      const cur = prev[classCode] ?? [];
      const next = checked ? [...cur, dim] : cur.filter((d) => d !== dim);
      return {
        ...prev,
        [classCode]: RULE_DIMENSIONS.filter((d) => next.includes(d)),
      };
    });
  };

  const handleSave = () => {
    const overrides: Record<string, string> = {};
    for (const cls of configurableClasses) {
      const dims = selections[cls.code] ?? [];
      if (dims.length > 0) {
        overrides[cls.code] = dims.join(",");
      }
    }
    updatePortfolio.mutate(
      { display_config: Object.keys(overrides).length ? overrides : null },
      { onSuccess: () => onOpenChange(false) }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>分组维度</DialogTitle>
          <DialogDescription>
            为各大类勾选参与持仓明细二级组合键拼接的维度；不勾选任何维度即按默认子集分组。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {configurableClasses.map((cls) => {
            const ruleDims = RULE_DIMENSIONS.filter(
              (d) => d in (dictData?.dimension_rules?.[cls.code] ?? {})
            );
            const selected = selections[cls.code] ?? [];
            const defaultDims = (DEFAULT_SUB_DIMS_BY_CLASS[cls.code] ?? []).filter(
              (d) => ruleDims.includes(d)
            );
            const dimNames = (dims: SubDimension[]) =>
              dims.map((d) => DIMENSION_LABELS[d] ?? d).join("·");
            const effectiveLabel =
              selected.length > 0
                ? dimNames(selected)
                : defaultDims.length > 0
                  ? `默认（${dimNames(defaultDims)}）`
                  : "默认（平铺）";
            return (
              <div
                key={cls.code}
                data-testid={`display-config-class-${cls.code}`}
                className="space-y-1.5"
              >
                <div className="flex items-center justify-between gap-4">
                  <span className="text-sm font-medium">{cls.name}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setSelections((prev) => ({ ...prev, [cls.code]: [] }))
                    }
                  >
                    恢复默认
                  </Button>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {ruleDims.map((d) => (
                    <label key={d} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={selected.includes(d)}
                        onCheckedChange={(checked) =>
                          toggleDim(cls.code, d, checked === true)
                        }
                      />
                      {DIMENSION_LABELS[d] ?? d}
                    </label>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">当前：{effectiveLabel}</p>
              </div>
            );
          })}
          {configurableClasses.length === 0 && (
            <p className="text-sm text-muted-foreground">暂无可配置的大类</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={handleSave}
            disabled={updatePortfolio.isPending || configurableClasses.length === 0}
          >
            {updatePortfolio.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}