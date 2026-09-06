"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ChevronDown, Loader2, SlidersHorizontal } from "lucide-react";
import { useProductList } from "@/hooks/useProduct";
import { cn, formatMarketName } from "@/lib/utils";
import ProductFilterDialog, { ProductSelection } from "@/components/shared/ProductFilterDialog";

interface ProductFilterSelectProps {
  variant?: "desktop" | "mobile";
  /** 当前已选产品（受控）；空数组 = 全部产品（归一由调用方处理） */
  value: ProductSelection[];
  /** 勾选/清空即时回传（变更即查询，不设确定按钮，规范 §9） */
  onChange: (v: ProductSelection[]) => void;
  /** 调用方传筛选栏宽度（h-9 w-[220px] / 移动 w-full） */
  className?: string;
}

const selectionKey = (s: ProductSelection) => `${s.code}|${s.market}`;

/**
 * 筛选栏产品多选合并组件（issue #162）：outline 触发器内三区——文本区/ChevronDown
 * 打开下拉直选（搜索 + checkbox 多选），SlidersHorizontal 打开高级筛选弹窗
 * （ProductFilterDialog）。勾选即时回传，无确定按钮。
 */
export default function ProductFilterSelect({
  variant = "desktop",
  value,
  onChange,
  className,
}: ProductFilterSelectProps) {
  const [open, setOpen] = useState(false);
  // 懒加载（#165）：首次打开后才挂产品查询；sticky 不回落，避免关闭后缓存丢弃
  const [hasOpened, setHasOpened] = useState(false);

  // 关键字防抖 300ms（规范 §9 文本输入类）
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(keywordInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  const { data, isLoading, isFetching } = useProductList(
    { page_size: 50, keyword: keyword || undefined },
    { enabled: hasOpened }
  );
  const items = useMemo(() => data?.items ?? [], [data?.items]);

  // 名称缓存：置顶已选项回显 name (code)；跨条件/跨结果集累积，缺失时回退 code
  const nameByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of items) map.set(`${p.code}|${p.market ?? ""}`, p.name);
    return map;
  }, [items]);
  const [nameCache, setNameCache] = useState<Map<string, string>>(new Map());
  useEffect(() => {
    setNameCache((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const [k, v] of nameByKey) {
        if (next.get(k) !== v) {
          next.set(k, v);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [nameByKey]);

  const isChecked = (s: ProductSelection) =>
    value.some((v) => v.code === s.code && v.market === s.market);

  const toggle = (s: ProductSelection) => {
    onChange(isChecked(s) ? value.filter((v) => selectionKey(v) !== selectionKey(s)) : [...value, s]);
  };

  // 已选项不在当前结果集时固定显示在列表顶部（跨条件保留，语义同筛选弹窗）
  const resultKeys = new Set(items.map((p) => `${p.code}|${p.market ?? ""}`));
  const pinned = value.filter((v) => !resultKeys.has(selectionKey(v)));

  const renderRow = (key: string, name: string | undefined, s: ProductSelection) => (
    <div
      key={key}
      className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 hover:bg-muted"
      onClick={() => toggle(s)}
    >
      {/* checkbox 自身点击阻止冒泡避免双重切换 */}
      <span onClick={(e) => e.stopPropagation()}>
        <Checkbox
          checked={isChecked(s)}
          onCheckedChange={() => toggle(s)}
          aria-label={`选择 ${name ?? s.code}`}
        />
      </span>
      <span className="min-w-0 flex-1 truncate text-sm">
        {name ? `${name} (${s.code})` : s.code}
        {s.market ? (
          <span className="text-muted-foreground"> · {formatMarketName(s.market)}</span>
        ) : null}
      </span>
    </div>
  );

  return (
    <div
      className={cn(
        "flex h-9 items-stretch overflow-hidden rounded-md border border-input bg-background text-sm",
        className
      )}
    >
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) setHasOpened(true);
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            className="flex min-w-0 flex-1 items-center justify-between gap-1 px-3 text-left font-normal transition-colors hover:bg-muted/50"
          >
            <span className={cn("truncate", !value.length && "text-muted-foreground")}>
              {value.length ? `产品 · 已选 ${value.length}` : "全部产品"}
            </span>
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          </button>
        </PopoverTrigger>
        {/* #328：宽度跟随触发框（Radix 注入 trigger 宽度变量），min-w-56 保底窄触发框下搜索框可读
            （触发按钮为容器内 flex-1 区，224px 与 220px 外层容器视觉齐平） */}
        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] min-w-56 p-0">
          <div className="relative border-b p-2">
            {isFetching && (
              <Loader2 className="absolute right-4 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
            )}
            <Input
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              placeholder="搜索产品代码/名称"
              className="h-8"
            />
          </div>
          <div className="max-h-64 overflow-y-auto p-1">
            {pinned.length > 0 && (
              <>
                {pinned.map((v) =>
                  renderRow(`pinned-${selectionKey(v)}`, nameCache.get(selectionKey(v)), v)
                )}
                <div className="my-1 border-t" />
              </>
            )}
            {isLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : items.length === 0 && pinned.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">无符合条件的产品</p>
            ) : (
              items.map((p) =>
                renderRow(
                  selectionKey({ code: p.code, market: p.market ?? "" }),
                  p.name,
                  { code: p.code, market: p.market ?? "" }
                )
              )
            )}
          </div>
          <div className="flex items-center justify-between border-t px-3 py-2">
            <span className="text-xs text-muted-foreground">已选 {value.length}</span>
            <Button
              variant="ghost"
              size="sm"
              className="h-7"
              disabled={!value.length}
              onClick={() => onChange([])}
            >
              清空
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      <ProductFilterDialog variant={variant} value={value} onConfirm={onChange}>
        <button
          type="button"
          aria-label="高级筛选"
          title="高级筛选"
          className="flex w-9 shrink-0 items-center justify-center border-l border-input text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        >
          <SlidersHorizontal className="h-4 w-4" />
        </button>
      </ProductFilterDialog>
    </div>
  );
}
