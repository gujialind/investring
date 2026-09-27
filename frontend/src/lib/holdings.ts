/**
 * #595 组合详情页「按产品」视图的分组聚合（纯函数）。
 *
 * 语义与 PositionSections 的持仓行分组一致（防 #109/#114 的 V4 契约）：
 * - 分区顺序由 asset_class 维度字典 sort_order 驱动（色板序位）；
 * - 二级分组维度经 resolveSubDim（组合级 display_config 覆盖优先，缺键回退内置默认）；
 * - 组间按合计市值降序、未分类恒垫底；组内产品按市值降序；
 * - 组名与大类同名（现金平铺、其他桶）时由组件判定不渲染 chip。
 */

import type { HoldingProductAggregate } from "@/types/holding";
import type { AssetClassificationItem } from "@/types/asset-classification";
import { resolveSubDim, type SubDimension } from "@/lib/dimensions";

export const UNCATEGORIZED_GROUP_NAME = "未分类";
export const OTHER_SECTION_NAME = "其他";

export interface HoldingProductGroup {
  /** 维度组名（平铺组 = 大类名，与大类同名时不渲染 chip） */
  name: string;
  products: HoldingProductAggregate[];
  total: number;
}

export interface HoldingProductSection {
  /** 大类 code；「其他」桶为 null */
  code: string | null;
  name: string;
  /** 大类 sort_order（色板序位）；「其他」桶为 null */
  sortOrder: number | null;
  subDim: SubDimension | null;
  groups: HoldingProductGroup[];
  total: number;
}

function groupProducts(
  items: HoldingProductAggregate[],
  dim: SubDimension | null,
  fallbackName: string
): HoldingProductGroup[] {
  const nameKey = dim ? (`${dim}_name` as const) : null;
  const byName = new Map<string, HoldingProductAggregate[]>();
  for (const p of items) {
    const name = nameKey ? p[nameKey] ?? UNCATEGORIZED_GROUP_NAME : fallbackName;
    const arr = byName.get(name) ?? [];
    arr.push(p);
    byName.set(name, arr);
  }
  const groups: HoldingProductGroup[] = [...byName.entries()].map(([name, ps]) => {
    const sorted = [...ps].sort((a, b) => b.market_value - a.market_value);
    return {
      name,
      products: sorted,
      total: sorted.reduce((s, p) => s + p.market_value, 0),
    };
  });
  groups.sort((a, b) => {
    if (a.name === UNCATEGORIZED_GROUP_NAME) return 1;
    if (b.name === UNCATEGORIZED_GROUP_NAME) return -1;
    return b.total - a.total;
  });
  return groups;
}

export function buildProductSections(
  products: HoldingProductAggregate[],
  assetClasses: AssetClassificationItem[],
  displayConfig?: Record<string, string> | null
): HoldingProductSection[] {
  const sortedClasses = [...assetClasses].sort((a, b) => a.sort_order - b.sort_order);
  const knownCodes = new Set(assetClasses.map((a) => a.code));

  const sections: HoldingProductSection[] = [];
  const rest: HoldingProductAggregate[] = [];
  for (const ac of sortedClasses) {
    const items = products.filter((p) => p.asset_class_code === ac.code);
    if (!items.length) continue;
    const subDim = resolveSubDim(displayConfig, ac.code);
    const groups = groupProducts(items, subDim, ac.name);
    sections.push({
      code: ac.code,
      name: ac.name,
      sortOrder: ac.sort_order,
      subDim,
      groups,
      total: groups.reduce((s, g) => s + g.total, 0),
    });
  }
  // 「其他」桶：asset_class 不在字典（或缺失）的产品恒垫底、平铺
  for (const p of products) {
    if (!p.asset_class_code || !knownCodes.has(p.asset_class_code)) rest.push(p);
  }
  if (rest.length) {
    const groups = groupProducts(rest, null, OTHER_SECTION_NAME);
    sections.push({
      code: null,
      name: OTHER_SECTION_NAME,
      sortOrder: null,
      subDim: null,
      groups,
      total: groups.reduce((s, g) => s + g.total, 0),
    });
  }
  return sections;
}

/** 组/分区占比：名下产品 ratio 之和转百分数（后端 ratio 为 0-1 小数，含在途基数） */
export function sumGroupPercent(products: HoldingProductAggregate[]): number {
  return products.reduce((s, p) => s + (p.ratio ?? 0), 0) * 100;
}
