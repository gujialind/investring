"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Check, Loader2, Pencil, Plus } from "lucide-react";
import {
  ApiException,
  assetClassificationApi,
  getErrorMessage,
  AssetClassificationItem,
  AssetClassificationUpdate,
  DimensionRule,
} from "@/lib/api";
import {
  useAssetClassifications,
  useCreateAssetClassification,
  useUpdateAssetClassification,
} from "@/hooks/useAssetClassification";
import { useUIStore } from "@/stores/uiStore";
import { cn } from "@/lib/utils";
import { DIMENSION_LABELS, RULE_DIMENSIONS, RULE_LABELS } from "@/lib/dimensions";
import LoadingState from "@/components/shared/LoadingState";
import RowActionButton from "@/components/shared/RowActionButton";

const DIMENSION_DESCRIPTIONS: Record<string, string> = {
  region: "产品投资地区（股票/债券必填）",
  style: "股票风格（仅股票）",
  size: "股票规模（仅股票）",
  segment: "细分领域：股票行业 / 债券期限 / 商品品种",
};

/** code 前缀 ↔ dimension（与后端校验一致，仅作表单提示） */
const CODE_PREFIX_OF_DIMENSION: Record<string, string> = {
  asset_class: "ASSET_",
  region: "REGION_",
  style: "STYLE_",
  size: "SIZE_",
  segment: "SEG_",
};

interface FormState {
  code: string;
  dimension: string;
  name: string;
  sort_order: number;
  description: string;
  is_active: boolean;
  applicable: string[];
  /** 仅 asset_class：dimension -> rule；无键 = forbidden */
  rules: Record<string, DimensionRule>;
}

const EMPTY_FORM: FormState = {
  code: "",
  dimension: "region",
  name: "",
  sort_order: 0,
  description: "",
  is_active: true,
  applicable: [],
  rules: {},
};

interface AssetClassificationsContentProps {
  variant?: "desktop" | "mobile";
}

export default function AssetClassificationsContent({
  variant = "desktop",
}: AssetClassificationsContentProps) {
  const isMobile = variant === "mobile";
  const addToast = useUIStore((state) => state.addToast);
  const queryClient = useQueryClient();

  const { data, isLoading } = useAssetClassifications();
  const createItem = useCreateAssetClassification();
  const updateItem = useUpdateAssetClassification();

  const items = data?.items ?? [];
  const dimensionRules = data?.dimension_rules ?? {};
  // API 已按 dimension + sort_order + code 排序，过滤即保序
  const classes = items.filter((i) => i.dimension === "asset_class");

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<AssetClassificationItem | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  /** 大类对该维度是否开放（无规则行 = forbidden） */
  const dimensionAllowed = (assetClass: string, dimension: string) =>
    dimensionRules[assetClass]?.[dimension] != null;

  // ---- 矩阵格子切换（适用关联全量替换 ±1；引用保护 422 → toast 列产品） ----
  const toggleLink = useMutation({
    mutationFn: ({ code, data: payload }: { code: string; data: AssetClassificationUpdate }) =>
      assetClassificationApi.update(code, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-classifications"] });
    },
    onError: (error: unknown) => {
      let message = getErrorMessage(error, "请稍后重试");
      if (error instanceof ApiException && Array.isArray(error.details?.products)) {
        message = `${message}：${(error.details.products as string[]).join("、")}`;
      }
      addToast({ type: "error", title: "调整适用关系失败", message });
    },
  });

  const handleToggle = (item: AssetClassificationItem, assetClass: string) => {
    const current = item.applicable_asset_classes;
    const next = current.includes(assetClass)
      ? current.filter((c) => c !== assetClass)
      : [...current, assetClass];
    toggleLink.mutate({ code: item.code, data: { applicable_asset_classes: next } });
  };

  // ---- 新建 / 编辑弹窗 ----
  const openCreate = () => {
    setEditingItem(null);
    setForm(EMPTY_FORM);
  };

  const openEdit = (item: AssetClassificationItem) => {
    setEditingItem(item);
    setForm({
      code: item.code,
      dimension: item.dimension,
      name: item.name,
      sort_order: item.sort_order,
      description: item.description ?? "",
      is_active: item.is_active,
      applicable: item.applicable_asset_classes,
      rules: { ...(dimensionRules[item.code] ?? {}) },
    });
    setIsDialogOpen(true);
  };

  const isAssetClass = form.dimension === "asset_class";
  // 非 asset_class 维度必须保留 ≥1 适用大类（与后端一致的前置提示）
  const applicableInvalid = !isAssetClass && form.applicable.length === 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (applicableInvalid) return;
    if (editingItem) {
      const payload: AssetClassificationUpdate = {
        name: form.name,
        sort_order: form.sort_order,
        description: form.description || null,
        is_active: form.is_active,
      };
      if (isAssetClass) {
        payload.dimension_rules = form.rules;
      } else {
        payload.applicable_asset_classes = form.applicable;
      }
      updateItem.mutate(
        { code: editingItem.code, data: payload },
        { onSuccess: () => setIsDialogOpen(false) }
      );
    } else {
      createItem.mutate(
        {
          code: form.code,
          dimension: form.dimension,
          name: form.name,
          sort_order: form.sort_order,
          description: form.description || undefined,
          ...(isAssetClass
            ? Object.keys(form.rules).length > 0
              ? { dimension_rules: form.rules }
              : {}
            : { applicable_asset_classes: form.applicable }),
        },
        { onSuccess: () => setIsDialogOpen(false) }
      );
    }
  };

  const toggleFormApplicable = (assetClass: string) => {
    setForm((f) => ({
      ...f,
      applicable: f.applicable.includes(assetClass)
        ? f.applicable.filter((c) => c !== assetClass)
        : [...f.applicable, assetClass],
    }));
  };

  const setFormRule = (dimension: string, value: string) => {
    setForm((f) => {
      const rules = { ...f.rules };
      if (value === "") {
        delete rules[dimension];
      } else {
        rules[dimension] = value as DimensionRule;
      }
      return { ...f, rules };
    });
  };

  const rulesSummary = (assetClass: string) => {
    const rules = dimensionRules[assetClass] ?? {};
    const parts = RULE_DIMENSIONS.filter((d) => rules[d]).map(
      (d) => `${DIMENSION_LABELS[d]}${RULE_LABELS[rules[d]!]}`
    );
    return parts.length > 0 ? parts.join("，") : "全禁止（现金型）";
  };

  const pending = createItem.isPending || updateItem.isPending;

  if (isLoading) {
    return <LoadingState />;
  }

  /** 状态徽标（停用 = neutral，见 visual-spec §1.3） */
  const inactiveBadge = (item: AssetClassificationItem) =>
    !item.is_active && <Badge variant="neutral">停用</Badge>;

  /** 矩阵单元格：forbidden → —；否则适用开关 */
  const matrixCell = (item: AssetClassificationItem, assetClass: string) => {
    if (!dimensionAllowed(assetClass, item.dimension)) {
      return <span className="text-muted-foreground">—</span>;
    }
    const linked = item.applicable_asset_classes.includes(assetClass);
    return (
      <button
        type="button"
        title={`${item.name} ↔ ${assetClass}`}
        disabled={toggleLink.isPending}
        onClick={() => handleToggle(item, assetClass)}
        className={cn(
          "inline-flex h-6 w-6 items-center justify-center rounded-md border",
          linked
            ? "border-primary bg-primary text-primary-foreground"
            : "border-input bg-background hover:bg-muted"
        )}
      >
        {linked && <Check className="h-4 w-4" />}
      </button>
    );
  };

  /** 移动端：值卡片下的大类勾选 chips（仅该维度开放的大类可点） */
  const applicableChips = (item: AssetClassificationItem) => {
    const allowed = classes.filter((c) => dimensionAllowed(c.code, item.dimension));
    if (allowed.length === 0) {
      return <span className="text-xs text-muted-foreground">无开放大类</span>;
    }
    return (
      <div className="flex flex-wrap gap-2">
        {allowed.map((c) => {
          const linked = item.applicable_asset_classes.includes(c.code);
          return (
            <button
              key={c.code}
              type="button"
              disabled={toggleLink.isPending}
              onClick={() => handleToggle(item, c.code)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs",
                linked
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-input text-muted-foreground"
              )}
            >
              {c.name}
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div className={isMobile ? "space-y-4" : "space-y-6"}>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">资产分类</h1>
          <p className="text-sm text-muted-foreground">
            五维正交维度字典与适用关系矩阵（点击格子调整值 ↔ 大类适用关系）
          </p>
        </div>
        <Dialog
          open={isDialogOpen}
          onOpenChange={(open) => {
            setIsDialogOpen(open);
            if (!open) setEditingItem(null);
          }}
        >
          <DialogTrigger asChild>
            <Button onClick={openCreate}>
              <Plus className="mr-2 h-4 w-4" />
              新建维度值
            </Button>
          </DialogTrigger>
          <DialogContent className="max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{editingItem ? `编辑 ${editingItem.code}` : "新建维度值"}</DialogTitle>
              <DialogDescription>
                {editingItem
                  ? "code 与维度不可改；适用关联与维度规则为全量替换"
                  : "code 全大写，前缀须匹配维度；无删除，后悔药用停用"}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit}>
              <div className="space-y-4 py-4">
                {!editingItem && (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="ac-dimension">维度</Label>
                      <select
                        id="ac-dimension"
                        value={form.dimension}
                        onChange={(e) =>
                          setForm({ ...form, dimension: e.target.value, applicable: [], rules: {} })
                        }
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                      >
                        {Object.entries(DIMENSION_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}（{CODE_PREFIX_OF_DIMENSION[value]}*）
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="ac-code">代码</Label>
                      <Input
                        id="ac-code"
                        value={form.code}
                        onChange={(e) => setForm({ ...form, code: e.target.value })}
                        placeholder={`${CODE_PREFIX_OF_DIMENSION[form.dimension]}XXX（全大写）`}
                        required
                      />
                    </div>
                  </>
                )}
                <div className="space-y-2">
                  <Label htmlFor="ac-name">名称</Label>
                  <Input
                    id="ac-name"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ac-sort-order">排序号</Label>
                  <Input
                    id="ac-sort-order"
                    type="number"
                    value={form.sort_order}
                    onChange={(e) =>
                      setForm({ ...form, sort_order: parseInt(e.target.value) || 0 })
                    }
                    required
                  />
                  {isAssetClass && (
                    <p className="text-xs text-warning">
                      大类的排序号即饼图/分区色板序位，变更即改色
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ac-description">描述</Label>
                  <Input
                    id="ac-description"
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </div>
                {editingItem && (
                  <div className="flex items-center gap-2">
                    <input
                      id="ac-is-active"
                      type="checkbox"
                      checked={form.is_active}
                      onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                      className="h-4 w-4 rounded border-input"
                    />
                    <Label htmlFor="ac-is-active">启用（停用后新建/变更引用被拒，存量引用不受影响）</Label>
                  </div>
                )}
                {isAssetClass ? (
                  <div className="space-y-2">
                    <Label>维度规则（禁止的维度该大类下产品不可填）</Label>
                    {RULE_DIMENSIONS.map((d) => (
                      <div key={d} className="flex items-center justify-between gap-2">
                        <span className="text-sm">{DIMENSION_LABELS[d]}</span>
                        <select
                          value={form.rules[d] ?? ""}
                          onChange={(e) => setFormRule(d, e.target.value)}
                          className="flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm"
                        >
                          <option value="">禁止</option>
                          <option value="optional">选填</option>
                          <option value="required">必填</option>
                        </select>
                      </div>
                    ))}
                    <p className="text-xs text-muted-foreground">
                      收紧（改必填/禁止）若与存量产品冲突将被后端拒绝并列出冲突产品
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label>适用大类（至少一个；目标大类须开放该维度）</Label>
                    <div className="flex flex-wrap gap-2">
                      {classes.map((c) => {
                        const allowed = editingItem
                          ? dimensionAllowed(c.code, form.dimension)
                          : true;
                        const checked = form.applicable.includes(c.code);
                        return (
                          <button
                            key={c.code}
                            type="button"
                            disabled={!allowed}
                            title={allowed ? c.code : `${c.name} 禁止${DIMENSION_LABELS[form.dimension]}维度`}
                            onClick={() => toggleFormApplicable(c.code)}
                            className={cn(
                              "rounded-full border px-3 py-1 text-xs",
                              !allowed && "cursor-not-allowed opacity-40",
                              checked
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-input text-muted-foreground"
                            )}
                          >
                            {c.name}
                          </button>
                        );
                      })}
                    </div>
                    {applicableInvalid && (
                      <p className="text-xs text-destructive">非大类维度必须选择至少一个适用大类</p>
                    )}
                  </div>
                )}
              </div>
              <DialogFooter>
                <Button type="submit" disabled={pending || applicableInvalid}>
                  {pending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {editingItem ? "保存修改" : "创建"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {/* 大区：asset_class 管理（规则摘要 + 编辑） */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">大类</CardTitle>
          <CardDescription>排序号即饼图/分区色板序位；规则决定该大类产品的必填/可填维度</CardDescription>
        </CardHeader>
        <CardContent className={isMobile ? "p-3 space-y-3" : undefined}>
          {isMobile ? (
            classes.map((c) => (
              <div key={c.code} className="rounded-lg border p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{c.name}</span>
                    {inactiveBadge(c)}
                  </div>
                  <RowActionButton icon={Pencil} label="编辑" onClick={() => openEdit(c)} />
                </div>
                <p className="text-xs text-muted-foreground">
                  {c.code} · 排序 {c.sort_order} · {rulesSummary(c.code)}
                </p>
              </div>
            ))
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>代码</TableHead>
                  <TableHead className="number-cell">排序</TableHead>
                  <TableHead>维度规则</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {classes.map((c) => (
                  <TableRow key={c.code}>
                    <TableCell>{c.name}</TableCell>
                    <TableCell className="text-muted-foreground">{c.code}</TableCell>
                    <TableCell className="number-cell">{c.sort_order}</TableCell>
                    <TableCell className="text-xs">{rulesSummary(c.code)}</TableCell>
                    <TableCell>
                      {c.is_active ? (
                        <Badge variant="success">启用</Badge>
                      ) : (
                        <Badge variant="neutral">停用</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <RowActionButton icon={Pencil} label="编辑" onClick={() => openEdit(c)} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* 四个维度的适用矩阵 */}
      {RULE_DIMENSIONS.map((dimension) => {
        const values = items.filter((i) => i.dimension === dimension);
        return (
          <Card key={dimension}>
            <CardHeader>
              <CardTitle className="text-lg font-semibold">{DIMENSION_LABELS[dimension]}</CardTitle>
              <CardDescription>{DIMENSION_DESCRIPTIONS[dimension]}</CardDescription>
            </CardHeader>
            <CardContent className={isMobile ? "p-3 space-y-3" : undefined}>
              {isMobile ? (
                values.map((v) => (
                  <div key={v.code} className="rounded-lg border p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{v.name}</span>
                        {inactiveBadge(v)}
                      </div>
                      <RowActionButton icon={Pencil} label="编辑" onClick={() => openEdit(v)} />
                    </div>
                    <p className="text-xs text-muted-foreground">{v.code}</p>
                    {applicableChips(v)}
                  </div>
                ))
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>维度值</TableHead>
                      {classes.map((c) => (
                        <TableHead key={c.code} className="text-center">
                          {c.name}
                        </TableHead>
                      ))}
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {values.map((v) => (
                      <TableRow key={v.code}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <span>{v.name}</span>
                            <span className="text-xs text-muted-foreground">{v.code}</span>
                            {inactiveBadge(v)}
                          </div>
                        </TableCell>
                        {classes.map((c) => (
                          <TableCell key={c.code} className="text-center">
                            {matrixCell(v, c.code)}
                          </TableCell>
                        ))}
                        <TableCell className="text-right">
                          <RowActionButton icon={Pencil} label="编辑" onClick={() => openEdit(v)} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
