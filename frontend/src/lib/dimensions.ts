/**
 * 五维正交维度（issue #128 / #135）的共享 UI 常量。
 * 维度字典数据来自 GET /api/asset-classifications，此处放展示标签与
 * 二级组合键默认子集/子集解析（issue #144 / #915）的共享事实来源。
 */

import type { AssetClassificationItem } from "@/types/asset-classification";

/** 可规则化的四个维度（asset_class 自身不参与规则/适用关联） */
export const RULE_DIMENSIONS = ["region", "style", "size", "segment"] as const;

export type RuleDimension = (typeof RULE_DIMENSIONS)[number];

/** 维度筛选键：asset_class + 可规则化四维（产品五维筛选的全集） */
export type DimensionFilterKey = "asset_class" | RuleDimension;

/** 维度筛选集：缺键 / 显式 undefined 均为「未筛选」（undefined 供联动清空写入） */
export type DimensionFilters = Partial<Record<DimensionFilterKey, string>>;

/** 二级分组维度（= 可规则化四维；内置默认按大类固定，组合级覆盖见 #144/#915） */
export type SubDimension = RuleDimension;

/** 维度级适用矩阵形态（GET /api/asset-classifications 顶层 dimension_rules） */
export type DimensionRules = Record<string, Record<string, string>>;

/** 维度字段名（product 表五列） */
export const DIMENSION_FIELDS = {
  region: "region_code",
  style: "style_code",
  size: "size_code",
  segment: "segment_code",
} as const;

export const DIMENSION_LABELS: Record<string, string> = {
  asset_class: "大类",
  region: "地区",
  style: "风格",
  size: "规模",
  segment: "细分",
};

export const RULE_LABELS: Record<string, string> = {
  required: "必填",
  optional: "选填",
};

/**
 * 大类 → 二级组合键默认入选子集（#915 D3/D4，取代 #144 单维默认表）：
 * 股票/债券 region,segment、商品 segment、现金空（平铺）。
 * 默认子集必须是该大类非禁止维度的子集；运行期以 dimension_rules 为准
 * （resolveSubDims 传 rules 时按登记过滤），本表只是缺省口径。
 */
export const DEFAULT_SUB_DIMS_BY_CLASS: Record<string, SubDimension[]> = {
  ASSET_STOCK: ["region", "segment"],
  ASSET_BOND: ["region", "segment"],
  ASSET_COMMODITY: ["segment"],
  ASSET_CASH: [],
};

/**
 * 解析组合级 display_config 的显式子集串为维度数组（#915 Q1=A 逗号分隔）。
 * 仅做形态解析（拆分/strip/剔除非法项/去重/按全序归一），不做规则校验——
 * 弹窗回填用：存量脏值尽力回显合法部分，保存时由后端校验拒绝非法整串。
 */
export function parseDisplaySubset(value: string | null | undefined): SubDimension[] {
  if (typeof value !== "string" || !value.trim()) return [];
  const selected = new Set<SubDimension>();
  for (const part of value.split(",")) {
    const dim = part.trim();
    if ((RULE_DIMENSIONS as readonly string[]).includes(dim)) {
      selected.add(dim as SubDimension);
    }
  }
  return RULE_DIMENSIONS.filter((d) => selected.has(d));
}

/**
 * 合并组合级 display_config 覆盖与默认子集表（#915，取代 resolveSubDim）：
 * 显式子集优先；缺键/未传/非法值（空串、未知维度、重复项、规则未登记）
 * 防御回退默认子集——与写侧整串拒绝同语义，读侧不抛错、不白屏。
 * 返回恒按固定全序 region→style→size→segment（存量观感契约，SPEC §2.1）。
 * 传 dimensionRules 时默认子集也按登记过滤（规则事后收紧的防御）。
 */
export function resolveSubDims(
  displayConfig: Record<string, string> | null | undefined,
  classCode: string,
  dimensionRules?: DimensionRules | null
): SubDimension[] {
  const registered = dimensionRules
    ? Object.keys(dimensionRules[classCode] ?? {})
    : null;
  const fallback = (): SubDimension[] => {
    const def = DEFAULT_SUB_DIMS_BY_CLASS[classCode] ?? [];
    return registered ? def.filter((d) => registered.includes(d)) : [...def];
  };
  const override = displayConfig?.[classCode];
  if (typeof override !== "string" || !override.trim()) return fallback();
  const selected = new Set<SubDimension>();
  for (const part of override.split(",")) {
    const dim = part.trim();
    if (
      !dim ||
      !(RULE_DIMENSIONS as readonly string[]).includes(dim) ||
      (registered && !registered.includes(dim)) ||
      selected.has(dim as SubDimension)
    ) {
      return fallback();
    }
    selected.add(dim as SubDimension);
  }
  return RULE_DIMENSIONS.filter((d) => selected.has(d));
}

/**
 * 维度筛选下拉选项（issue #238 抽取，产品筛选弹窗与产品管理页共用）：
 * 启用值；选了 asset_class 则按 applicable_asset_classes 收窄。
 * dimension 含 "asset_class"：asset_class 维度值的 applicable_asset_classes 恒为空数组，
 * 但不参与收窄谓词（selectedAssetClass 未传时不判该项），故
 * getDimensionOptions(dictItems, "asset_class") 即「启用大类列表」的统一入口。
 */
export function getDimensionOptions(
  dictItems: AssetClassificationItem[],
  dimension: DimensionFilterKey,
  selectedAssetClass?: string
): AssetClassificationItem[] {
  return dictItems.filter(
    (i) =>
      i.dimension === dimension &&
      i.is_active &&
      (!selectedAssetClass || i.applicable_asset_classes.includes(selectedAssetClass))
  );
}

/**
 * 切换大类后的维度筛选集（issue #238 抽取）：返回新筛选集，
 * 不再适用新大类的维度值清空（避免查不出数据的隐形条件）；未选大类时仅更新 asset_class。
 */
export function clearInapplicableDims(
  dimFilters: DimensionFilters,
  newAssetClass: string | undefined,
  dictItems: AssetClassificationItem[]
): DimensionFilters {
  const next: DimensionFilters = { ...dimFilters, asset_class: newAssetClass };
  if (newAssetClass) {
    for (const dimension of RULE_DIMENSIONS) {
      const current = next[dimension];
      if (
        current &&
        !dictItems.some(
          (i) =>
            i.code === current &&
            i.is_active &&
            i.applicable_asset_classes.includes(newAssetClass)
        )
      ) {
        next[dimension] = undefined;
      }
    }
  }
  return next;
}
