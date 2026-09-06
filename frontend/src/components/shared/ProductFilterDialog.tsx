"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
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
import { Loader2 } from "lucide-react";
import { Product } from "@/types/product";
import { useProductList } from "@/hooks/useProduct";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import {
  DIMENSION_LABELS,
  RULE_DIMENSIONS,
  clearInapplicableDims,
  getDimensionOptions,
} from "@/lib/dimensions";
import type { DimensionFilterKey, DimensionFilters } from "@/lib/dimensions";
import { MARKET_OPTIONS } from "@/lib/market";
import { cn, formatMarketName } from "@/lib/utils";

/** 产品筛选选中项：code + market（market 为空串对应现金类等无市场产品） */
export interface ProductSelection {
  code: string;
  market: string;
}

const selectionKey = (s: ProductSelection) => `${s.code}|${s.market}`;

// 产品类型过滤选项（label 与产品表单下拉保持一致）；市场选项走 @/lib/market 共享（#324）
const PRODUCT_TYPE_OPTIONS = [
  { value: "ETF", label: "ETF" },
  { value: "OEF", label: "开放式基金" },
  { value: "LOF", label: "LOF" },
  { value: "CASH", label: "现金" },
] as const;

const productTypeLabel = (type: string) =>
  PRODUCT_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type;

interface ProductFilterDialogProps {
  variant?: "desktop" | "mobile";
  /** 当前已选产品（受控）；空数组 = 全部产品 */
  value: ProductSelection[];
  /** 点「确定」回传选择并关闭；「取消」/关闭不回传 */
  onConfirm: (selection: ProductSelection[]) => void;
  /** 触发器（由调用方给筛选栏风格按钮） */
  children: React.ReactNode;
}

/**
 * 产品多选筛选弹窗（issue #155，调仓页筛选栏）：关键字防抖搜索 + 五维/类型/市场
 * 条件过滤 + checkbox 多选。「全选/取消全选」只作用于当前查询结果集；已选集合
 * 跨条件变更保留，确定时才回传调用方。
 */
export default function ProductFilterDialog({
  variant = "desktop",
  value,
  onConfirm,
  children,
}: ProductFilterDialogProps) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Map<string, ProductSelection>>(new Map());

  // 关键字防抖 300ms（规范 §9 文本输入类）
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(keywordInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  // 条件行：asset_class + region/style/size/segment 四维 + product_type + market
  const [dimFilters, setDimFilters] = useState<DimensionFilters>({});
  const [productType, setProductType] = useState<string | undefined>(undefined);
  const [market, setMarket] = useState<string | undefined>(undefined);

  // 每次打开：从受控 value 重建选择集，条件与关键字重置
  useEffect(() => {
    if (open) {
      setSelected(new Map(value.map((v) => [selectionKey(v), v])));
      setKeywordInput("");
      setKeyword("");
      setDimFilters({});
      setProductType(undefined);
      setMarket(undefined);
    }
  }, [open, value]);

  // 条件变更即时查询（queryKey 带 params 自动刷新）
  const { data, isLoading, isFetching } = useProductList({
    page_size: 100,
    keyword: keyword || undefined,
    product_type: productType,
    market,
    asset_class_code: dimFilters.asset_class,
    region_code: dimFilters.region,
    style_code: dimFilters.style,
    size_code: dimFilters.size,
    segment_code: dimFilters.segment,
  });
  const resultItems = useMemo(() => data?.items ?? [], [data?.items]);

  // 维度字典：过滤下拉选项 + 产品五维 code→name 映射
  const { data: dictData } = useAssetClassifications();
  const dictItems = useMemo(() => dictData?.items ?? [], [dictData?.items]);
  const nameByCode = useMemo(() => new Map(dictItems.map((i) => [i.code, i.name])), [dictItems]);
  // 启用大类列表与维度选项同一入口（asset_class 维度不参与收窄谓词）
  const assetClasses = getDimensionOptions(dictItems, "asset_class");
  // 维度选项收窄与大类联动清空走 lib/dimensions 纯函数（#238 抽取，与产品管理页共用）
  const dimensionOptions = (dimension: DimensionFilterKey) =>
    getDimensionOptions(dictItems, dimension, dimFilters.asset_class);

  const handleAssetClassChange = (assetClass: string | undefined) => {
    setDimFilters((prev) => clearInapplicableDims(prev, assetClass, dictItems));
  };

  const toggleProduct = (product: Product) => {
    const selection = { code: product.code, market: product.market ?? "" };
    const key = selectionKey(selection);
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(key)) next.delete(key);
      else next.set(key, selection);
      return next;
    });
  };

  // 全选/取消全选仅作用于当前查询结果集
  const selectAllResults = () =>
    setSelected((prev) => {
      const next = new Map(prev);
      for (const p of resultItems) {
        const selection = { code: p.code, market: p.market ?? "" };
        next.set(selectionKey(selection), selection);
      }
      return next;
    });
  const clearResults = () =>
    setSelected((prev) => {
      const next = new Map(prev);
      for (const p of resultItems) next.delete(selectionKey({ code: p.code, market: p.market ?? "" }));
      return next;
    });

  /** 产品五维标签（code→name 走字典映射，无映射回显 code） */
  const dimensionTagsOf = (p: Product) =>
    [p.asset_class_code, p.region_code, p.style_code, p.size_code, p.segment_code]
      .filter((c): c is string => !!c)
      .map((c) => nameByCode.get(c) ?? c);

  const selectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[128px]";

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent
        className={cn(
          "max-h-[90vh] overflow-y-auto",
          variant === "desktop" && "max-w-2xl"
        )}
      >
          <DialogHeader>
            <DialogTitle>筛选产品</DialogTitle>
            <DialogDescription>按条件过滤后勾选产品，确定后生效</DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <Input
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              placeholder="搜索产品代码/名称"
              className="h-9"
            />

            {/* 条件行：桌面 flex-wrap，移动 grid-cols-2 纵向堆叠 */}
            <div className={variant === "mobile" ? "grid grid-cols-2 gap-2" : "flex flex-wrap gap-2"}>
              <Select
                value={dimFilters.asset_class ?? "all"}
                onValueChange={(v) => handleAssetClassChange(v === "all" ? undefined : v)}
              >
                <SelectTrigger className={selectWidth}>
                  <SelectValue placeholder="全部大类" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部大类</SelectItem>
                  {assetClasses.map((item) => (
                    <SelectItem key={item.code} value={item.code}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {RULE_DIMENSIONS.map((dimension) => (
                <Select
                  key={dimension}
                  value={dimFilters[dimension] ?? "all"}
                  onValueChange={(v) =>
                    setDimFilters({ ...dimFilters, [dimension]: v === "all" ? undefined : v })
                  }
                >
                  <SelectTrigger className={selectWidth}>
                    <SelectValue placeholder={`全部${DIMENSION_LABELS[dimension]}`} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">{`全部${DIMENSION_LABELS[dimension]}`}</SelectItem>
                    {dimensionOptions(dimension).map((item) => (
                      <SelectItem key={item.code} value={item.code}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ))}
              <Select
                value={productType ?? "all"}
                onValueChange={(v) => setProductType(v === "all" ? undefined : v)}
              >
                <SelectTrigger className={selectWidth}>
                  <SelectValue placeholder="全部类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部类型</SelectItem>
                  {PRODUCT_TYPE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={market ?? "all"}
                onValueChange={(v) => setMarket(v === "all" ? undefined : v)}
              >
                <SelectTrigger className={selectWidth}>
                  <SelectValue placeholder="全部市场" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部市场</SelectItem>
                  {MARKET_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">
                共 {data?.total ?? 0} 个产品 · 已选 {selected.size}
              </span>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-9"
                  onClick={selectAllResults}
                  disabled={resultItems.length === 0}
                >
                  全选
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-9"
                  onClick={clearResults}
                  disabled={resultItems.length === 0}
                >
                  取消全选
                </Button>
              </div>
            </div>

            {/* 结果列表：固定高度，加载/空态/有结果同高，条件切换不跳变（#163）；
                局部刷新保留旧数据 + 右上角小 spinner（规范 §14） */}
            <div className="relative">
              {isFetching && (
                <Loader2 className="absolute right-2 top-2 z-10 h-4 w-4 animate-spin text-muted-foreground" />
              )}
              <div
                className={cn(
                  "h-[40vh] overflow-y-auto rounded-md border px-3",
                  isFetching && "opacity-50"
                )}
              >
                {isLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : resultItems.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    无符合条件的产品
                  </p>
                ) : (
                  resultItems.map((p) => {
                    const key = selectionKey({ code: p.code, market: p.market ?? "" });
                    const checked = selected.has(key);
                    const tags = dimensionTagsOf(p);
                    return (
                      <div
                        key={key}
                        className="flex cursor-pointer items-start gap-2 border-b py-2 last:border-b-0"
                        onClick={() => toggleProduct(p)}
                      >
                        {/* 点击行即切换；checkbox 自身点击阻止冒泡避免双重切换 */}
                        <span className="pt-0.5" onClick={(e) => e.stopPropagation()}>
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => toggleProduct(p)}
                            aria-label={`选择 ${p.name}`}
                          />
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <p className="truncate text-sm font-medium">{p.name}</p>
                            {p.data_source && (
                              <span className="shrink-0 text-xs text-muted-foreground">
                                {p.data_source}
                              </span>
                            )}
                          </div>
                          {/* market 为空（CASH / 在途虚拟产品）不拼市场段，避免 `· --` 残留（#393） */}
                          <p className="text-xs text-muted-foreground">
                            {p.code}
                            {p.market ? <> · {formatMarketName(p.market)}</> : null}
                            {" · "}
                            {productTypeLabel(p.product_type)}
                          </p>
                          {tags.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {tags.map((tag) => (
                                <Badge key={tag} variant="neutral" className="font-normal">
                                  {tag}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => {
                onConfirm(Array.from(selected.values()));
                setOpen(false);
              }}
            >
              确定
            </Button>
          </DialogFooter>
        </DialogContent>
    </Dialog>
  );
}
