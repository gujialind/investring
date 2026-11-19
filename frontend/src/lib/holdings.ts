/**
 * #595 组合详情页「按产品」视图的分组聚合（纯函数）。
 *
 * 语义与 `PortfolioHoldings.tsx::ProductSections` 的持仓行分组一致（V4 契约见 #595/#109/#114）：
 * - 分区顺序由 asset_class 维度字典 sort_order 驱动（色板序位）；
 * - 二级分组为「维度子集组合键」（#915）：入选子集经 resolveViewSubDims（页面级
 *   临时覆盖优先〔B2，URL 视图状态、不写回配置〕，其次组合级 display_config 显式
 *   子集，缺键/非法回退默认子集表 D3/D4）；键 = 入选维
 *   code 元组（按 code 分组、按 name 以「·」拼接显示），仅由实际持仓生成；
 * - 一卡恰属一组（互斥划分，承重不变量）：合计/占比/饼图口径与单维时代逐字相同；
 * - 组间按合计市值降序、含占位符「未分类」的组恒垫底；组内产品按市值降序；
 * - 组名与大类同名（现金平铺、其他桶）时由组件判定不渲染 chip。
 */

import type { HoldingProductAggregate } from "@/types/holding";
import type { AssetClassificationItem } from "@/types/asset-classification";
import {
  resolveViewSubDims,
  type DimensionRules,
  type SubDimension,
  type SubDimOverrides,
} from "@/lib/dimensions";
import { PSEUDO_IN_TRANSIT_CODE } from "@/lib/allocation";
import { largestRemainderPercents } from "@/lib/utils";

export const UNCATEGORIZED_GROUP_NAME = "未分类";
export const OTHER_SECTION_NAME = "其他";
/** 组合键显示名分隔符（U+00B7，两侧不加空格；存量观感契约，SPEC §6.1） */
export const COMBO_KEY_SEPARATOR = "·";
/** code 元组键连接符（React key 与分组判重用，不参与显示） */
export const CODE_KEY_SEPARATOR = "|";
/** 平铺组（0 维大类/其他桶）的 codeKey 哨兵 */
export const FLAT_CODE_KEY = "__flat__";

export interface HoldingProductGroup {
  /** 组显示名：组合键 name 拼接；平铺组 = 大类名（与大类同名时不渲染 chip） */
  name: string;
  /** code 元组键（按 code 分组，同名不同 code 不合并）；平铺组为哨兵 */
  codeKey: string;
  /** 任一入选维槽位为占位位（code 空或 name 空）；排序用：含占位符的组恒垫底（SPEC §6.3） */
  hasPlaceholder: boolean;
  products: HoldingProductAggregate[];
  total: number;
}

export interface HoldingProductSection {
  /** 大类 code；「其他」桶为 null */
  code: string | null;
  name: string;
  /** 大类 sort_order（色板序位）；「其他」桶为 null */
  sortOrder: number | null;
  /** 该大类入选子集（全序）；空 = 平铺 */
  subDims: SubDimension[];
  groups: HoldingProductGroup[];
  total: number;
}

function groupProducts(
  items: HoldingProductAggregate[],
  dims: SubDimension[],
  fallbackName: string
): HoldingProductGroup[] {
  // 0 维子集（现金/其他桶）：平铺单组，组名 = 桶名，不渲染 chip
  if (dims.length === 0) {
    const sorted = [...items].sort((a, b) => b.market_value - a.market_value);
    return [
      {
        name: fallbackName,
        codeKey: FLAT_CODE_KEY,
        hasPlaceholder: false,
        products: sorted,
        total: sorted.reduce((s, p) => s + p.market_value, 0),
      },
    ];
  }
  const byCodeKey = new Map<string, HoldingProductAggregate[]>();
  for (const p of items) {
    const codes = dims.map((d) => p[`${d}_code`] ?? "");
    const codeKey = codes.join(CODE_KEY_SEPARATOR);
    const arr = byCodeKey.get(codeKey) ?? [];
    arr.push(p);
    byCodeKey.set(codeKey, arr);
  }
  const groups: HoldingProductGroup[] = [...byCodeKey.entries()].map(
    ([codeKey, ps]) => {
      const codes = codeKey.split(CODE_KEY_SEPARATOR);
      const names = dims.map((d) => ps[0][`${d}_name`] ?? "");
      // 占位位判据 = code 空 OR name 空（L2 r1 判据侧对齐：不依赖 code/name 恒一致
      // 的隐含前提；字典行缺失致 name 空而 code 有值时，与显示层「未分类」同判垫底）
      const placeholder = codes.map((c, i) => !c || !names[i]);
      const hasPlaceholder = placeholder.some(Boolean);
      // 入选维全为占位位 → 收敛为单个「未分类」（不重复占位符）；部分占位 → 空位填占位符
      const name = placeholder.every(Boolean)
        ? UNCATEGORIZED_GROUP_NAME
        : dims
            .map((d, i) => (placeholder[i] ? UNCATEGORIZED_GROUP_NAME : names[i]))
            .join(COMBO_KEY_SEPARATOR);
      const sorted = [...ps].sort((a, b) => b.market_value - a.market_value);
      return {
        name,
        codeKey,
        hasPlaceholder,
        products: sorted,
        total: sorted.reduce((s, p) => s + p.market_value, 0),
      };
    }
  );
  // 含占位符的组恒垫底，层内按 total 降序（现状「未分类恒垫底」的严格推广）
  groups.sort((a, b) => {
    if (a.hasPlaceholder !== b.hasPlaceholder) return a.hasPlaceholder ? 1 : -1;
    return b.total - a.total;
  });
  return groups;
}

export function buildProductSections(
  products: HoldingProductAggregate[],
  assetClasses: AssetClassificationItem[],
  displayConfig?: Record<string, string> | null,
  dimensionRules?: DimensionRules | null,
  /** 页面级临时子集覆盖（#915 B2，URL 视图状态）：优先于 displayConfig，不写回配置 */
  subDimOverrides?: SubDimOverrides | null
): HoldingProductSection[] {
  const sortedClasses = [...assetClasses].sort((a, b) => a.sort_order - b.sort_order);
  const knownCodes = new Set(assetClasses.map((a) => a.code));

  const sections: HoldingProductSection[] = [];
  const rest: HoldingProductAggregate[] = [];
  for (const ac of sortedClasses) {
    const items = products.filter((p) => p.asset_class_code === ac.code);
    if (!items.length) continue;
    const subDims = resolveViewSubDims(displayConfig, ac.code, dimensionRules, subDimOverrides);
    const groups = groupProducts(items, subDims, ac.name);
    sections.push({
      code: ac.code,
      name: ac.name,
      sortOrder: ac.sort_order,
      subDims,
      groups,
      total: groups.reduce((s, g) => s + g.total, 0),
    });
  }
  // 「其他」桶：asset_class 不在字典（或缺失）的产品恒垫底、平铺
  for (const p of products) {
    if (!p.asset_class_code || !knownCodes.has(p.asset_class_code)) rest.push(p);
  }
  if (rest.length) {
    const groups = groupProducts(rest, [], OTHER_SECTION_NAME);
    sections.push({
      code: null,
      name: OTHER_SECTION_NAME,
      sortOrder: null,
      subDims: [],
      groups,
      total: groups.reduce((s, g) => s + g.total, 0),
    });
  }
  return sections;
}

/** 产品卡稳定键（与 PortfolioHoldings 渲染 key 一致） */
export function productCardKey(p: HoldingProductAggregate): string {
  return `${p.product_code}-${p.market}`;
}

/**
 * 行级占比（§4 最大余数法，加总恒 100.0%）：行集 = 产品聚合卡（含现金卡）+
 * 在途聚合卡（市值 >0 时参与，#595 评审决策补回在途展示），全部行同一分配。
 * 返回 产品卡键 → 占比百分数（如 62.4）的 Map；在途卡以 PSEUDO_IN_TRANSIT_CODE 为键。
 */
export function buildCardPercentMap(
  products: HoldingProductAggregate[],
  inTransitMarketValue: number
): Map<string, number> {
  const keys = products.map(productCardKey);
  const amounts = products.map((p) => p.market_value);
  if (inTransitMarketValue > 0) {
    keys.push(PSEUDO_IN_TRANSIT_CODE);
    amounts.push(inTransitMarketValue);
  }
  const percents = largestRemainderPercents(amounts);
  return new Map(keys.map((key, i) => [key, percents[i]]));
}

/** 组/分区占比：名下产品卡行级占比之和（分区头/chip 由展示层取整，§4「加总后取整」） */
export function sumGroupPercent(
  products: HoldingProductAggregate[],
  percentOf: (p: HoldingProductAggregate) => number
): number {
  return products.reduce((s, p) => s + percentOf(p), 0);
}
