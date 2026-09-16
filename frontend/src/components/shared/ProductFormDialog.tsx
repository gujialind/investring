"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Loader2 } from "lucide-react";
import { Product, ProductCreate } from "@/types/product";
import { DIMENSION_FIELDS, DIMENSION_LABELS, RULE_DIMENSIONS } from "@/lib/dimensions";
import { MARKET_OPTIONS } from "@/lib/market";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import { useCreateProduct, useUpdateProduct } from "@/hooks/useProduct";

/** 创建态表单初始值（编辑态以 editingProduct 整体回填） */
const EMPTY_FORM: Partial<Product> = {
  code: "",
  market: "",
  name: "",
  product_type: "ETF",
  confirm_days: 1,
  // issue #228：快照估值取价滞后交易日数（0=当日；QDII/互认基金可设 1）
  nav_lag_days: 0,
  is_qdii: false,
};

/** 创建态 confirm_days 缺省推导（#231/#236/#241，镜像后端
 *  `backend/app/services/product_service.py::calculate_confirm_days`，规则变更需同步）：
 *  显式优先、未传推导——前端预填推导值如实下发，用户可覆盖 */
function deriveConfirmDays(market: string | undefined, isQdii: boolean | undefined): number {
  if (market === "CN_EXCHANGE") return 0;
  if (market === "CN_OTC" && isQdii) return 2;
  return 1;
}

interface ProductFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 编辑态传入产品；创建态传 null/undefined */
  editingProduct?: Product | null;
  /** 创建/更新成功后回调（toast 与产品列表失效由 hook 自带，此处供调用方追加动作） */
  onSuccess?: (product: Product) => void;
  /** 可选触发器（如「添加产品」按钮）；不传则完全受控 */
  trigger?: React.ReactNode;
}

/**
 * 产品创建/编辑表单对话框（issue #155 抽自 ProductsContent，供产品管理页与
 * 产品筛选弹窗共用）。表单字段、维度规则消费、提交行为与原实现一致。
 */
export default function ProductFormDialog({
  open,
  onOpenChange,
  editingProduct,
  onSuccess,
  trigger,
}: ProductFormDialogProps) {
  const createProduct = useCreateProduct();
  const updateProduct = useUpdateProduct();

  const [formData, setFormData] = useState<Partial<Product>>(EMPTY_FORM);
  // 创建态：用户手改过确认天数后，market/is_qdii 联动预填不再覆盖
  const [confirmDaysTouched, setConfirmDaysTouched] = useState(false);

  // 打开时按两态初始化表单：编辑回填整个产品，创建回初始值
  useEffect(() => {
    if (open) {
      setFormData(editingProduct ?? EMPTY_FORM);
      setConfirmDaysTouched(false);
    }
  }, [open, editingProduct]);

  // 维度字典驱动五维录入控件（issue #135）：选项按 applicable + is_active 过滤，
  // 必填/禁用读顶层 dimension_rules；后端 validate_dimension_tags 为兜底
  const { data: dictData } = useAssetClassifications();
  const dictItems = dictData?.items ?? [];
  const dimensionRules = dictData?.dimension_rules ?? {};
  const assetClasses = dictItems.filter((i) => i.dimension === "asset_class" && i.is_active);
  const currentClass = formData.asset_class_code || "";
  const ruleOf = (dimension: string): "required" | "optional" | null =>
    currentClass ? dimensionRules[currentClass]?.[dimension] ?? null : null;
  /** 维度下拉选项：启用且适用当前大类；编辑回填的现值即使停用/不适用也保留显示 */
  const dimensionOptions = (dimension: string) => {
    const field = DIMENSION_FIELDS[dimension as keyof typeof DIMENSION_FIELDS];
    const current = formData[field];
    return dictItems.filter(
      (i) =>
        i.dimension === dimension &&
        ((i.is_active && currentClass && i.applicable_asset_classes.includes(currentClass)) ||
          (current != null && i.code === current))
    );
  };

  /** 切换大类：禁用维度清空；不适用/停用值重置 */
  const handleClassChange = (assetClass: string) => {
    const next: Partial<Product> = { ...formData, asset_class_code: assetClass || undefined };
    for (const dimension of RULE_DIMENSIONS) {
      const field = DIMENSION_FIELDS[dimension];
      const rule = assetClass ? dimensionRules[assetClass]?.[dimension] ?? null : null;
      const current = next[field];
      if (rule === null) {
        next[field] = undefined;
      } else if (current) {
        const stillValid = dictItems.some(
          (i) =>
            i.code === current &&
            i.is_active &&
            i.applicable_asset_classes.includes(assetClass)
        );
        if (!stillValid) next[field] = undefined;
      }
    }
    setFormData(next);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (editingProduct) {
      updateProduct.mutate(
        {
          code: editingProduct.code,
          market: editingProduct.market,
          data: {
            name: formData.name,
            // issue #232：产品类型可纠错（后端枚举校验）
            product_type: formData.product_type,
            // 维度字段空值发 null 显式清除（后端 exclude_unset 下 null 进入合并校验）
            asset_class_code: formData.asset_class_code || null,
            region_code: formData.region_code || null,
            style_code: formData.style_code || null,
            size_code: formData.size_code || null,
            segment_code: formData.segment_code || null,
            confirm_days: formData.confirm_days,
            nav_lag_days: formData.nav_lag_days,
            is_qdii: formData.is_qdii,
          },
        },
        {
          onSuccess: (product) => {
            onOpenChange(false);
            onSuccess?.(product);
          },
        }
      );
    } else {
      // 空字符串维度字段不下发（后端按缺省 NULL 处理；market 缺省由后端归一为 ""）
      const createPayload: ProductCreate = {
        code: formData.code ?? "",
        name: formData.name ?? "",
        product_type: formData.product_type ?? "ETF",
        confirm_days: formData.confirm_days,
        nav_lag_days: formData.nav_lag_days,
        is_qdii: formData.is_qdii,
        ...(formData.market ? { market: formData.market } : {}),
        ...(formData.asset_class_code ? { asset_class_code: formData.asset_class_code } : {}),
        ...(formData.region_code ? { region_code: formData.region_code } : {}),
        ...(formData.style_code ? { style_code: formData.style_code } : {}),
        ...(formData.size_code ? { size_code: formData.size_code } : {}),
        ...(formData.segment_code ? { segment_code: formData.segment_code } : {}),
      };
      createProduct.mutate(createPayload, {
        onSuccess: (product) => {
          onOpenChange(false);
          onSuccess?.(product);
        },
      });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editingProduct ? "编辑产品" : "添加产品"}</DialogTitle>
          <DialogDescription>
            {editingProduct ? "修改产品信息" : "创建新的产品"}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="code">产品代码</Label>
              <Input
                id="code"
                value={formData.code}
                onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                disabled={!!editingProduct}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="market">市场</Label>
              <select
                id="market"
                value={formData.market}
                onChange={(e) => {
                  const market = e.target.value;
                  setFormData({
                    ...formData,
                    market,
                    // 创建态预填联动：后端显式优先（#231/#236/#241），默认 1 对场内会 422
                    ...(!editingProduct && !confirmDaysTouched
                      ? { confirm_days: deriveConfirmDays(market, formData.is_qdii) }
                      : {}),
                  });
                }}
                disabled={!!editingProduct}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <option value="">无（现金类）</option>
                {/* 与筛选弹窗/产品管理页/交易选择器共用同一定义（PR #502 评审），
                    禁止再硬编码市场中文名——label 由 formatMarketName 单一派生 */}
                {MARKET_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="name">产品名称</Label>
              <Input
                id="name"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="product_type">产品类型</Label>
              <select
                id="product_type"
                value={formData.product_type}
                onChange={(e) => setFormData({ ...formData, product_type: e.target.value })}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <option value="ETF">ETF</option>
                <option value="OEF">开放式基金</option>
                <option value="LOF">LOF</option>
                <option value="CASH">现金</option>
                {/* issue #232：现值不在选项表（如虚拟产品 IN_TRANSIT）补占位项，避免显示空白 */}
                {formData.product_type &&
                  !["ETF", "OEF", "LOF", "CASH"].includes(formData.product_type) && (
                    <option value={formData.product_type}>{formData.product_type}</option>
                  )}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="asset_class_code">资产大类</Label>
              <select
                id="asset_class_code"
                value={formData.asset_class_code ?? ""}
                onChange={(e) => handleClassChange(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <option value="">无（现金/虚拟产品）</option>
                {assetClasses.map((ac) => (
                  <option key={ac.code} value={ac.code}>
                    {ac.name}
                  </option>
                ))}
                {/* 现值已停用/不在启用列表时补占位项，避免下拉显示错位 */}
                {formData.asset_class_code &&
                  !assetClasses.some((ac) => ac.code === formData.asset_class_code) && (
                    <option value={formData.asset_class_code}>
                      {dictItems.find((i) => i.code === formData.asset_class_code)?.name ??
                        formData.asset_class_code}
                      （已停用）
                    </option>
                  )}
              </select>
            </div>
            {RULE_DIMENSIONS.map((dimension) => {
              const rule = ruleOf(dimension);
              if (!rule) return null;
              const field = DIMENSION_FIELDS[dimension];
              const options = dimensionOptions(dimension);
              const current = formData[field];
              return (
                <div className="space-y-2" key={dimension}>
                  <Label htmlFor={field}>
                    {DIMENSION_LABELS[dimension]}
                    {rule === "required" && <span className="text-destructive"> *</span>}
                  </Label>
                  <select
                    id={field}
                    value={current ?? ""}
                    onChange={(e) =>
                      setFormData({ ...formData, [field]: e.target.value || undefined })
                    }
                    required={rule === "required"}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="">未设置</option>
                    {options.map((item) => (
                      <option key={item.code} value={item.code}>
                        {item.name}
                      </option>
                    ))}
                    {current && !options.some((i) => i.code === current) && (
                      <option value={current}>
                        {dictItems.find((i) => i.code === current)?.name ?? current}（已停用）
                      </option>
                    )}
                  </select>
                </div>
              );
            })}
            <div className="space-y-2">
              <Label htmlFor="confirm_days">确认天数</Label>
              <Input
                id="confirm_days"
                type="number"
                min={0}
                step={1}
                value={formData.confirm_days ?? ""}
                onChange={(e) => {
                  // 空值/非法输入回落预填推导值，避免 NaN 进入表单状态（同 nav_lag_days）
                  const parsed = parseInt(e.target.value, 10);
                  const valid = !Number.isNaN(parsed);
                  setFormData({
                    ...formData,
                    confirm_days: valid ? parsed : deriveConfirmDays(formData.market, formData.is_qdii),
                  });
                  // 仅有效输入算手改（回落不阻断后续 market/is_qdii 预填联动）
                  if (valid) setConfirmDaysTouched(true);
                }}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="nav_lag_days">估值滞后交易日</Label>
              <Input
                id="nav_lag_days"
                type="number"
                min={0}
                step={1}
                value={formData.nav_lag_days ?? 0}
                onChange={(e) => {
                  // 空值/非法输入回落 0，避免 NaN 进入表单状态
                  const parsed = parseInt(e.target.value, 10);
                  setFormData({ ...formData, nav_lag_days: Number.isNaN(parsed) ? 0 : parsed });
                }}
                required
              />
              <p className="text-xs text-muted-foreground">
                快照取价滞后：普通产品 0，QDII/互认基金可设 1
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                id="is_qdii"
                type="checkbox"
                checked={formData.is_qdii}
                onChange={(e) => {
                  const isQdii = e.target.checked;
                  setFormData({
                    ...formData,
                    is_qdii: isQdii,
                    // 创建态预填联动（场外+QDII→2，同后端推导规则）
                    ...(!editingProduct && !confirmDaysTouched
                      ? { confirm_days: deriveConfirmDays(formData.market, isQdii) }
                      : {}),
                  });
                }}
                className="h-4 w-4 rounded border-gray-300"
              />
              <Label htmlFor="is_qdii">QDII基金</Label>
            </div>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={createProduct.isPending || updateProduct.isPending}>
              {(createProduct.isPending || updateProduct.isPending) && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {editingProduct ? "保存修改" : "创建产品"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
