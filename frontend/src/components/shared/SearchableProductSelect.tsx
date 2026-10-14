"use client";

import { useEffect, useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Check, ChevronDown, Loader2 } from "lucide-react";
import { useProductList } from "@/hooks/useProduct";
import { MARKET_OPTIONS } from "@/lib/market";
import { cn, formatMarketName, formatProductDisplayCode } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { ProductSelection } from "@/components/shared/ProductFilterDialog";

interface SearchableProductSelectProps {
  /** 单选；null = 未选 */
  value: ProductSelection | null;
  onChange: (v: ProductSelection | null) => void;
  placeholder?: string;
  /** 触发按钮 id，供外层 Label htmlFor 关联（可访问性） */
  id?: string;
}

/** 选项回显缓存项（#705）：名称与显示码都只来自产品列表结果集 */
type ProductMeta = { name: string; displayCode: string };

/**
 * 表单单选可搜索下拉（issue #162，提交交易表单）：h-10 触发按钮 + Popover
 * 关键词防抖搜索 + 点选即回传并关闭。选项粒度 code|market（LOF 一码多市场分行）。
 * 市场标识（#259）：选项行三段式——名称可截断 / (显示码) 独立不截断 / 尾部市场
 * Badge（market 为空不渲染），行挂完整 title；选中回显「名称 (显示码) · 市场名」，
 * 名称截断、后缀不截断，触发按钮挂 title。E2E 定位：product-option + data-code/data-market
 * （data-code 恒为库内原始码，#705 显示码只上人眼文案）。
 * 市场筛选（#324）：搜索框与列表之间一行 Select（全部市场/A股场内/内地场外/香港互认），
 * 服务端过滤（market 参数），数据层消除 LOF 一码多市场歧义；筛选不持久——
 * 点选即弃、重开复位「全部市场」，不同步 market、不加拦截。
 * ⚠️ Popover modal=false 而 Radix Select 为 modal：Select 打开时焦点移到其 portal
 * （Popover 外）会触发外层 focus-outside 误关弹层，PopoverContent 须
 * onInteractOutside preventDefault 阻止（Radix 社区公认方案）。
 */
export default function SearchableProductSelect({
  value,
  onChange,
  placeholder = "请选择产品",
  id,
}: SearchableProductSelectProps) {
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

  // 市场筛选（#324）：undefined = 全部市场；不持久——重开复位（见 onOpenChange）
  const [market, setMarket] = useState<string | undefined>(undefined);

  const { data, isLoading, isFetching } = useProductList(
    { page_size: 50, keyword: keyword || undefined, market: market || undefined },
    { enabled: hasOpened }
  );
  const items = useMemo(() => data?.items ?? [], [data?.items]);

  // 名称/显示码缓存：选中项回显 name (显示码)（#705）；两者仅来自当前/历史结果集，
  // 缺失时名称回退显示码、显示码回退本地剥后缀（港互认销售码前端无来源，只能等结果集带到）
  const metaByKey = useMemo(() => {
    const map = new Map<string, ProductMeta>();
    for (const p of items)
      map.set(`${p.code}|${p.market ?? ""}`, {
        name: p.name,
        displayCode: p.display_code || formatProductDisplayCode(p.code, p.market),
      });
    return map;
  }, [items]);
  const [metaCache, setMetaCache] = useState<Map<string, ProductMeta>>(new Map());
  useEffect(() => {
    setMetaCache((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const [k, v] of metaByKey) {
        const cur = next.get(k);
        if (!cur || cur.name !== v.name || cur.displayCode !== v.displayCode) {
          next.set(k, v);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [metaByKey]);

  const selectionKey = (s: ProductSelection) => `${s.code}|${s.market}`;
  const isSelected = (s: ProductSelection) =>
    !!value && value.code === s.code && value.market === s.market;

  // 选中回显（#259）：「名称 (显示码)」基础标签 + 可选「 · 市场名」后缀；
  // market 为空不拼后缀（无 `· --` 残留）；名称部分 truncate、后缀 shrink-0，
  // 触发按钮 span 挂 title 全文本供悬停查看被截断部分
  const baseLabel = value
    ? (() => {
        const meta = metaCache.get(selectionKey(value));
        const shown = meta?.displayCode ?? formatProductDisplayCode(value.code, value.market);
        return meta?.name ? `${meta.name} (${shown})` : shown;
      })()
    : placeholder;
  const marketSuffix = value?.market ? formatMarketName(value.market) : "";
  const fullLabel = marketSuffix ? `${baseLabel} · ${marketSuffix}` : baseLabel;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setHasOpened(true);
          // 筛选不持久（#324）：无论上次的关闭路径（点选/外部点击/Esc），重开一律复位
          setMarket(undefined);
        }
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          id={id}
          className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm font-normal ring-offset-background transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <span
            className={cn("flex min-w-0 flex-1 items-center", !value && "text-muted-foreground")}
            title={value ? fullLabel : undefined}
          >
            <span className="min-w-0 truncate">{baseLabel}</span>
            {marketSuffix && <span className="shrink-0">{` · ${marketSuffix}`}</span>}
          </span>
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      {/* onInteractOutside preventDefault：内部市场 Select（modal）打开时焦点/点击
          落在其 body portal（Popover 外），阻止外层 Popover 误判 outside 交互关闭（#324）；
          #328：宽度跟随触发框（Radix 注入 trigger 宽度变量），min-w-56 保底搜索框可读 */}
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] min-w-56 p-0"
        onInteractOutside={(e) => e.preventDefault()}
      >
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
        {/* 市场条件行（#324）：服务端过滤，LOF 按市场分行后数据层消除一码多市场歧义 */}
        <div className="border-b p-2">
          <Select
            value={market ?? "all"}
            onValueChange={(v) => setMarket(v === "all" ? undefined : v)}
          >
            <SelectTrigger className="h-8 w-full">
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
        <div className="max-h-64 overflow-y-auto p-1">
          {isLoading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : items.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">无符合条件的产品</p>
          ) : (
            items.map((p) => {
              const s = { code: p.code, market: p.market ?? "" };
              // 市场标识（#259）：尾部独立徽章，market 为空不渲染；悬停 title 给出
              // 完整「名称 (code) · 市场」，长名称截断不丢信息
              const marketName = s.market ? formatMarketName(s.market) : "";
              // #705 选项行代码用显示码（选择结果 data-code / onChange 仍传原始码）
              const shownCode = p.display_code || formatProductDisplayCode(p.code, s.market);
              return (
                <div
                  key={selectionKey(s)}
                  data-testid="product-option"
                  data-code={p.code}
                  data-market={s.market}
                  title={marketName ? `${p.name} (${shownCode}) · ${marketName}` : `${p.name} (${shownCode})`}
                  className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 hover:bg-muted"
                  onClick={() => {
                    onChange(s);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "h-4 w-4 shrink-0",
                      isSelected(s) ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate text-sm">{p.name}</span>
                  <span className="shrink-0 text-sm text-muted-foreground">({shownCode})</span>
                  {s.market && (
                    <Badge variant="neutral" className="shrink-0">
                      {marketName}
                    </Badge>
                  )}
                </div>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
