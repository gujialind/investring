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

/** 二级组合键可选维度（= 可规则化四维；默认子集表见 DEFAULT_SUB_DIMS_BY_CLASS，组合级显式子集覆盖见 #915） */
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
 * 按登记矩阵过滤显式子集（L2 r1：弹窗回填/提交与 resolveSubDims 同一登记判据
 * d ∈ dimensionRules[classCode]），仅保留已登记维度、保持传入全序。
 * 与 resolveSubDims 的整串回退不同：弹窗须尽力回显合法已登记部分（勾选态），
 * 读侧分组对脏串仍整串回退默认——两处语义各自成文，不混用。
 * 不传 rules 时不做登记过滤（形态解析结果原样返回）。
 */
export function filterRegisteredDims(
  dims: SubDimension[],
  classCode: string,
  dimensionRules?: DimensionRules | null
): SubDimension[] {
  if (!dimensionRules) return [...dims];
  const registered = Object.keys(dimensionRules[classCode] ?? {});
  return dims.filter((d) => registered.includes(d));
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
    // own-property 判据：classCode 撞原型成员（constructor 等）时 ?? [] 不生效、
    // def 会拿到 Object 函数致 .filter 抛错（ocr r1 同类白屏链，B1 遗留一并收口）
    const def = Object.prototype.hasOwnProperty.call(DEFAULT_SUB_DIMS_BY_CLASS, classCode)
      ? DEFAULT_SUB_DIMS_BY_CLASS[classCode]
      : [];
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
 * 页面级临时子集覆盖（#915 B2）：大类 code → 维度子集（全序）。
 * 视图状态，经 URL ?sub= 持久化（与 ?view= 先例同构），**不写回 display_config**、
 * 不引入浏览器存储/个人偏好（SPEC §9.4 C-F 读法：URL 参数属视图状态）。
 * 缺键 = 该大类跟随组合配置/默认子集（resolveSubDims）。
 */
export type SubDimOverrides = Record<string, SubDimension[]>;

/**
 * 解析 URL ?sub= 参数为临时子集覆盖（#915 B2）。
 * 形态：`大类code.维.维~大类code.维`（逻辑分隔符；URLSearchParams 形式编码会把
 * `~` 写成 %7E、get 时解码还原，单大类条目不含分隔符、地址栏可读）。
 * 仅做形态解析（未知维度剔除/去重/按全序归一），不做规则校验——与
 * parseDisplaySubset 同哲学；登记校验在 resolveViewSubDims 消费时做。
 * 无有效维度的条目整条丢弃（空子集不支持强制平铺，Q8）。
 * 原型敏感键（__proto__/constructor/prototype）整条丢弃：classCode 来自
 * 用户可控 URL，plain-object 赋值 __proto__ 会改原型链（ocr r1）。
 */
export function parseSubDimOverrides(raw: string | null): SubDimOverrides {
  if (!raw) return {};
  const result: SubDimOverrides = {};
  for (const entry of raw.split("~")) {
    const [classCode, ...dims] = entry.split(".");
    if (
      !classCode ||
      classCode === "__proto__" ||
      classCode === "constructor" ||
      classCode === "prototype"
    ) {
      continue;
    }
    const selected = new Set<SubDimension>();
    for (const dim of dims) {
      if ((RULE_DIMENSIONS as readonly string[]).includes(dim)) {
        selected.add(dim as SubDimension);
      }
    }
    if (selected.size === 0) continue;
    result[classCode] = RULE_DIMENSIONS.filter((d) => selected.has(d));
  }
  return result;
}

/**
 * 序列化临时子集覆盖为 URL ?sub= 值（#915 B2）：大类 code 字典序、维度全序，
 * 同一覆盖集恒产出同一串（可作缓存/断言锚）；空覆盖返回 null（调用方移除参数）。
 */
export function serializeSubDimOverrides(overrides: SubDimOverrides): string | null {
  const entries: string[] = [];
  for (const classCode of Object.keys(overrides).sort()) {
    const dims = RULE_DIMENSIONS.filter((d) => overrides[classCode]?.includes(d));
    if (dims.length > 0) entries.push(`${classCode}.${dims.join(".")}`);
  }
  return entries.length > 0 ? entries.join("~") : null;
}

/**
 * 切换某大类的一个入选维（#915 B2 快捷切换 + 页面直切子集，同一交互）：
 * - 该大类无覆盖（默认态）时首击 = 快捷切换到该单维（不是在默认子集上追加）；
 * - 已有覆盖时点击为勾选/取消（多选子集）；
 * - 取消最后一维 = 移除该大类覆盖、回默认（Q8 不支持强制平铺）。
 * 返回新覆盖集（不改入参），恒按全序归一。
 */
export function toggleSubDimOverride(
  overrides: SubDimOverrides,
  classCode: string,
  dim: SubDimension
): SubDimOverrides {
  // 与 parse 同判据：原型敏感键不接受写入（防 plain-object 原型链污染）
  if (classCode === "__proto__" || classCode === "constructor" || classCode === "prototype") {
    return { ...overrides };
  }
  // Array.isArray 守卫：继承成员（如 constructor）不是子集，按无覆盖处理
  const current = Array.isArray(overrides[classCode]) ? overrides[classCode] : [];
  const next = current.includes(dim)
    ? current.filter((d) => d !== dim)
    : [...current, dim];
  const result = { ...overrides };
  if (next.length === 0) delete result[classCode];
  else result[classCode] = RULE_DIMENSIONS.filter((d) => next.includes(d));
  return result;
}

/**
 * 消费侧生效子集解析（#915 B2）：页面临时覆盖优先于组合配置/默认子集表。
 * 覆盖含规则未登记维（规则事后收紧的防御）时整条失效、回退 resolveSubDims——
 * 与读侧对脏配置「整串回退默认」同语义（SPEC §3.3），不抛错、不白屏。
 * 空覆盖/缺键同样走 resolveSubDims。不传 overrides 时行为与 resolveSubDims 全等。
 */
export function resolveViewSubDims(
  displayConfig: Record<string, string> | null | undefined,
  classCode: string,
  dimensionRules?: DimensionRules | null,
  overrides?: SubDimOverrides | null
): SubDimension[] {
  const override = overrides?.[classCode];
  // Array.isArray 守卫：classCode 若撞继承成员（如字典值 code=constructor），
  // 无守卫时 override=Object 函数、.every 抛 TypeError 白屏（ocr r1）
  if (Array.isArray(override) && override.length > 0) {
    const registered = dimensionRules
      ? Object.keys(dimensionRules[classCode] ?? {})
      : null;
    if (!registered || override.every((d) => registered.includes(d))) {
      return RULE_DIMENSIONS.filter((d) => override.includes(d));
    }
  }
  return resolveSubDims(displayConfig, classCode, dimensionRules);
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
