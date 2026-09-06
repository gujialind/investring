"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import Link from "next/link";
import { Plus, Pencil, Trash2, CheckCircle, XCircle, Loader2, RefreshCw, TrendingUp, Eye, Tags, Filter } from "lucide-react";
import { Product } from "@/types/product";
import { useUIStore } from "@/stores/uiStore";
import { cn, formatNav } from "@/lib/utils";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import ProductFormDialog from "@/components/shared/ProductFormDialog";
import EmptyState from "@/components/shared/EmptyState";
import PaginationBar from "@/components/shared/PaginationBar";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import {
  DIMENSION_LABELS,
  RULE_DIMENSIONS,
  clearInapplicableDims,
  getDimensionOptions,
} from "@/lib/dimensions";
import type { DimensionFilters } from "@/lib/dimensions";
import { MARKET_OPTIONS } from "@/lib/market";
import type { ProductListParams } from "@/lib/api";
import {
  useProductList,
  useDeleteProduct,
  useProductPrices,
  useSyncProductPrice,
  useSyncProductHistory,
} from "@/hooks/useProduct";

interface ProductsContentProps {
  variant?: "desktop" | "mobile";
}

// 筛选选项（label 与产品筛选弹窗/产品表单下拉保持一致）；市场选项走 @/lib/market 共享（#324）
const PRODUCT_TYPE_OPTIONS = [
  { value: "ETF", label: "ETF" },
  { value: "OEF", label: "开放式基金" },
  { value: "LOF", label: "LOF" },
  { value: "CASH", label: "现金" },
] as const;

const CONFIRM_DAYS_OPTIONS = [0, 1, 2];

const NAV_LAG_OPTIONS = [
  { value: 0, label: "T" },
  { value: 1, label: "T-1" },
] as const;

const DATA_SOURCE_OPTIONS = ["tushare", "akshare"];

/**
 * 产品管理页内容（桌面/移动共用，issue #234 真分页 + #238 筛选栏）。
 * 筛选/分页全部走服务端参数（queryKey 带 listParams 自动刷新）；
 * 五维筛选的选项收窄与大类联动清空走 lib/dimensions 纯函数（与产品筛选弹窗共用）。
 */
export default function ProductsContent({ variant = "desktop" }: ProductsContentProps) {
  const addToast = useUIStore((state) => state.addToast);

  // 分页状态（#234：替代原 page_size:100 假分页）
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // 关键字防抖 300ms（规范 §9 文本输入类）
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(keywordInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  // 筛选状态（#238）：空值 = undefined，axios 不传参
  const [market, setMarket] = useState<string | undefined>(undefined);
  const [productType, setProductType] = useState<string | undefined>(undefined);
  const [confirmDays, setConfirmDays] = useState<number | undefined>(undefined);
  const [navLagDays, setNavLagDays] = useState<number | undefined>(undefined);
  const [dataSource, setDataSource] = useState<string | undefined>(undefined);
  const [isQdii, setIsQdii] = useState<boolean | undefined>(undefined);
  const [dimFilters, setDimFilters] = useState<DimensionFilters>({});
  const [filterOpen, setFilterOpen] = useState(false);

  const listParams: ProductListParams = {
    page,
    page_size: pageSize,
    keyword: keyword || undefined,
    market,
    product_type: productType,
    confirm_days: confirmDays,
    nav_lag_days: navLagDays,
    data_source: dataSource,
    is_qdii: isQdii,
    asset_class_code: dimFilters.asset_class,
    region_code: dimFilters.region,
    style_code: dimFilters.style,
    size_code: dimFilters.size,
    segment_code: dimFilters.segment,
    // 管理页展示全部（含 CASH/IN_TRANSIT 系统虚拟产品）；#327 起后端默认排除
    include_virtual: true,
  };
  const { data, isLoading, isFetching, isError } = useProductList(listParams);

  const deleteProduct = useDeleteProduct();

  const products = data?.items || [];
  const total = data?.total ?? 0;

  // 页码越界钳制（评审 #244）：删除/筛选失效后 total 收缩致 page 越界时回退末页，
  // 避免「末页删空即死胡同」；只在 data 到达后判（placeholderData 期间 total 为旧值，不误钳）
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => {
    if (data && page > totalPages) {
      setPage(totalPages);
    }
  }, [data, page, totalPages]);

  // 维度字典：五维筛选下拉选项（启用值 + 按所选大类收窄；失败 toast 由 hook 弹出）
  const { data: dictData } = useAssetClassifications();
  const dictItems = useMemo(() => dictData?.items ?? [], [dictData?.items]);
  // 启用大类列表与维度选项同一入口（asset_class 维度不参与收窄谓词）
  const assetClasses = getDimensionOptions(dictItems, "asset_class");

  // 非默认筛选判定：驱动「重置」按钮显隐、空态文案二分与移动端激活计数。
  // hasKeyword 是 keyword ∨ keywordInput 的并集，两边缺一不可：
  // keywordInput 覆盖「已输入但防抖未生效」窗口（用户感知已有筛选，重置按钮须立即可见）；
  // keyword 覆盖「输入刚清空但防抖未追平」窗口（列表仍按旧关键字过滤，不能误判成无筛选）。
  // 重构时若删掉任一半边，对应窗口期的重置按钮显隐与空态文案会被误判。
  const hasKeyword = keyword !== "" || keywordInput.trim() !== "";
  const hasNonDefaultFilter =
    hasKeyword ||
    market !== undefined ||
    productType !== undefined ||
    confirmDays !== undefined ||
    navLagDays !== undefined ||
    dataSource !== undefined ||
    isQdii !== undefined ||
    Object.values(dimFilters).some((v) => v !== undefined);
  const activeFilterCount =
    (hasKeyword ? 1 : 0) +
    (market ? 1 : 0) +
    (productType ? 1 : 0) +
    (confirmDays !== undefined ? 1 : 0) +
    (navLagDays !== undefined ? 1 : 0) +
    (dataSource ? 1 : 0) +
    (isQdii !== undefined ? 1 : 0) +
    Object.values(dimFilters).filter((v) => v !== undefined).length;

  const resetFilters = () => {
    setKeywordInput("");
    setKeyword("");
    setMarket(undefined);
    setProductType(undefined);
    setConfirmDays(undefined);
    setNavLagDays(undefined);
    setDataSource(undefined);
    setIsQdii(undefined);
    setDimFilters({});
    setPage(1);
  };

  // 创建/编辑共用 ProductFormDialog（issue #155 抽取）：editingProduct 为 null 即创建态
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);

  const handleEdit = (product: Product) => {
    setEditingProduct(product);
    setIsDialogOpen(true);
  };

  const handleDelete = (code: string, market?: string) => {
    setPendingDelete({ code, market });
  };

  const [pendingDelete, setPendingDelete] = useState<{ code: string; market?: string } | null>(null);

  const [priceDialogOpen, setPriceDialogOpen] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);

  const syncPrice = useSyncProductPrice();
  const syncHistory = useSyncProductHistory();

  const { data: priceData, isLoading: priceLoading } = useProductPrices(
    priceDialogOpen ? selectedProduct?.code : undefined,
    selectedProduct?.market,
    30
  );

  const handleSyncPrice = (product: Product) => {
    if (!product.market) {
      addToast({
        type: "error",
        title: "同步失败",
        message: "现金类产品无需同步价格",
      });
      return;
    }
    syncPrice.mutate({ code: product.code, market: product.market });
  };

  const handleSyncHistory = (product: Product) => {
    if (!product.market) {
      addToast({
        type: "error",
        title: "同步失败",
        message: "现金类产品无需同步历史",
      });
      return;
    }
    syncHistory.mutate({ code: product.code, market: product.market });
  };

  const handleViewPrices = (product: Product) => {
    setSelectedProduct(product);
    setPriceDialogOpen(true);
  };

  // 筛选栏控件（规范 §9：表格卡片内顶部，控件 h-9、无 Label、placeholder 表意）；
  // 下拉走 ui/select（「全部 X」用 "all" 哨兵，Radix SelectItem 不允许空串值），
  // 数字型在 onChange 里 Number(v)（0 是合法筛选值，不能用真值判断）；每次变更页码归 1
  const selectWidth = variant === "mobile" ? "h-9 w-full" : "h-9 w-[140px]";
  const filterControls = (
    <>
      <Input
        value={keywordInput}
        onChange={(e) => {
          setKeywordInput(e.target.value);
          setPage(1);
        }}
        placeholder="搜索产品代码/名称"
        className={variant === "mobile" ? "h-9 w-full" : "h-9 w-[200px]"}
      />
      <Select
        value={market ?? "all"}
        onValueChange={(v) => {
          setMarket(v === "all" ? undefined : v);
          setPage(1);
        }}
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
      <Select
        value={productType ?? "all"}
        onValueChange={(v) => {
          setProductType(v === "all" ? undefined : v);
          setPage(1);
        }}
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
        value={confirmDays === undefined ? "all" : String(confirmDays)}
        onValueChange={(v) => {
          setConfirmDays(v === "all" ? undefined : Number(v));
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="确认天数" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部确认天数</SelectItem>
          {CONFIRM_DAYS_OPTIONS.map((d) => (
            <SelectItem key={d} value={String(d)}>
              {d} 天
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={navLagDays === undefined ? "all" : String(navLagDays)}
        onValueChange={(v) => {
          setNavLagDays(v === "all" ? undefined : Number(v));
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="估值滞后" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部估值滞后</SelectItem>
          {NAV_LAG_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={String(o.value)}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={dataSource ?? "all"}
        onValueChange={(v) => {
          setDataSource(v === "all" ? undefined : v);
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="数据源" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部数据源</SelectItem>
          {DATA_SOURCE_OPTIONS.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={isQdii === undefined ? "all" : String(isQdii)}
        onValueChange={(v) => {
          setIsQdii(v === "all" ? undefined : v === "true");
          setPage(1);
        }}
      >
        <SelectTrigger className={selectWidth}>
          <SelectValue placeholder="QDII" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部</SelectItem>
          <SelectItem value="true">是 QDII</SelectItem>
          <SelectItem value="false">非 QDII</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={dimFilters.asset_class ?? "all"}
        onValueChange={(v) => {
          // 切换大类：不再适用新大类的维度值联动清空（lib/dimensions 纯函数）
          setDimFilters((prev) => clearInapplicableDims(prev, v === "all" ? undefined : v, dictItems));
          setPage(1);
        }}
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
          onValueChange={(v) => {
            setDimFilters({ ...dimFilters, [dimension]: v === "all" ? undefined : v });
            setPage(1);
          }}
        >
          <SelectTrigger className={selectWidth}>
            <SelectValue placeholder={`全部${DIMENSION_LABELS[dimension]}`} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{`全部${DIMENSION_LABELS[dimension]}`}</SelectItem>
            {getDimensionOptions(dictItems, dimension, dimFilters.asset_class).map((item) => (
              <SelectItem key={item.code} value={item.code}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ))}
      {hasNonDefaultFilter && (
        <Button variant="ghost" size="sm" className="h-9" onClick={resetFilters}>
          重置
        </Button>
      )}
    </>
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">产品管理</h1>
          <p className="text-muted-foreground">
            管理基金产品和数据源
          </p>
        </div>
        <ProductFormDialog
          open={isDialogOpen}
          onOpenChange={setIsDialogOpen}
          editingProduct={editingProduct}
          trigger={
            <Button onClick={() => setEditingProduct(null)}>
              <Plus className="mr-2 h-4 w-4" />
              添加产品
            </Button>
          }
        />
      </div>

      {/* 移动端入口：分类矩阵管理页（PC 端走侧边栏「分类」） */}
      <Link href="/m/asset-classifications" className="lg:hidden block">
        <Button variant="outline" size="sm" className="w-full">
          <Tags className="mr-2 h-4 w-4" />
          资产分类管理
        </Button>
      </Link>

      <Card>
        <CardHeader>
          <CardTitle>产品列表</CardTitle>
          <CardDescription>
            所有可投资的产品
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* 筛选栏（规范 §9）；移动端为折叠面板 + 激活计数 Badge */}
          {variant === "mobile" ? (
            <div className="mb-3 space-y-2">
              <Button variant="outline" size="sm" className="h-9" onClick={() => setFilterOpen((v) => !v)}>
                <Filter className="mr-2 h-4 w-4" />
                筛选
                {activeFilterCount > 0 && (
                  <Badge variant="default" className="ml-2">
                    {activeFilterCount}
                  </Badge>
                )}
              </Button>
              {filterOpen && <div className="grid grid-cols-1 gap-2">{filterControls}</div>}
            </div>
          ) : (
            <div className="mb-3 flex flex-wrap items-center gap-2">{filterControls}</div>
          )}
          {/* 规范 §14：筛选/翻页局部刷新保留旧数据，表格半透明 + 右上角小 spinner；
              首次加载（isLoading）走上方区块级 spinner，此处不重复遮罩；
              移动端窄屏表格横向滚动（同 SnapshotsContent） */}
          <div className={cn("relative", variant === "mobile" && "overflow-x-auto")}>
            {isFetching && !isLoading && (
              <Loader2 className="absolute right-2 top-2 z-10 h-4 w-4 animate-spin text-muted-foreground" />
            )}
            <Table className={cn(isFetching && !isLoading && "opacity-50")}>
              <TableHeader>
                <TableRow>
                  <TableHead>代码</TableHead>
                  <TableHead>市场</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead className="number-cell">确认天数</TableHead>
                  <TableHead className="number-cell">估值滞后</TableHead>
                  <TableHead>QDII</TableHead>
                  <TableHead>数据源</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {products.map((product) => (
                  <TableRow key={`${product.code}-${product.market || "null"}`}>
                    <TableCell className="font-medium">{product.code}</TableCell>
                    <TableCell>{product.market || "--"}</TableCell>
                    <TableCell>{product.name}</TableCell>
                    <TableCell>{product.product_type}</TableCell>
                    <TableCell className="number-cell">{product.confirm_days}</TableCell>
                    {/* issue #228：快照估值取价日，0 显示 T，N 显示 T-N */}
                    <TableCell className="number-cell">
                      {(product.nav_lag_days ?? 0) > 0 ? `T-${product.nav_lag_days}` : "T"}
                    </TableCell>
                    <TableCell>{product.is_qdii ? "是" : "否"}</TableCell>
                    <TableCell>
                      {product.data_source_status === "success" ? (
                        <CheckCircle className="h-4 w-4 text-success" />
                      ) : product.data_source_status === "failed" ? (
                        <XCircle className="h-4 w-4 text-destructive" />
                      ) : (
                        <span className="text-warning">待验证</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleEdit(product)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(product.code, product.market)}
                        disabled={deleteProduct.isPending}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                      {product.market && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleSyncPrice(product)}
                            disabled={syncPrice.isPending}
                          >
                            {syncPrice.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <RefreshCw className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleSyncHistory(product)}
                            disabled={syncHistory.isPending}
                          >
                            {syncHistory.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <TrendingUp className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleViewPrices(product)}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {/* 空态三分：请求失败 ≠ 空数据（评审 #244；toast 已由 hook 弹出，此处内联区分）；
              无筛选为空 = 暂无产品；有筛选为空 = 引导重置（规范 §8 变体②） */}
          {products.length === 0 &&
            (isError ? (
              <EmptyState message="产品列表加载失败" description="请检查网络后重试" />
            ) : hasNonDefaultFilter ? (
              <EmptyState
                message="无符合筛选条件的记录"
                action={
                  <Button variant="ghost" size="sm" onClick={resetFilters}>
                    重置筛选
                  </Button>
                }
              />
            ) : (
              <EmptyState message="暂无产品" />
            ))}
          <PaginationBar
            page={page}
            pageSize={pageSize}
            total={total}
            variant={variant}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        </CardContent>
      </Card>

      <Dialog open={priceDialogOpen} onOpenChange={setPriceDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              {selectedProduct?.name} ({selectedProduct?.code}) 价格走势
            </DialogTitle>
            <DialogDescription>
              最近 30 条价格记录
            </DialogDescription>
          </DialogHeader>
          {priceLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : priceData && priceData.length > 0 ? (
            <div className="rounded-md border max-h-96 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>日期</TableHead>
                    <TableHead className="number-cell">价格/净值</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {priceData.map((record) => (
                    <TableRow key={record.price_date}>
                      <TableCell>{record.price_date}</TableCell>
                      <TableCell className="number-cell font-medium">
                        {formatNav(record.unit_price)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <TrendingUp className="h-8 w-8 mb-2" />
              <p>暂无价格数据，请先同步价格</p>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!pendingDelete}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="删除产品"
        description="确定要删除该产品吗？已被持仓或交易引用的产品无法删除。"
        confirmText="删除"
        onConfirm={() => {
          if (pendingDelete) deleteProduct.mutate(pendingDelete);
          setPendingDelete(null);
        }}
      />
    </div>
  );
}
