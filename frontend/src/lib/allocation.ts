import type { Position } from "@/types/position";
import type { AssetClassificationItem } from "@/types/asset-classification";
import { assetClassColor, IN_TRANSIT_COLOR, OTHER_COLOR } from "@/lib/colors";
import { largestRemainderPercents } from "@/lib/utils";

/**
 * 资产大类聚合（issue #128 字典驱动）：饼图图例与持仓分区头共用的单一计算入口。
 *
 * 大类口径：asset_class 维度字典（股票/债券/商品/现金）+ 在途 + 其他。
 * - 买入/卖出/分红在途抽出为独立「在途」伪大类，不与现金混合；
 * - 基金/CASH 行按读侧派生的 asset_class_code 归大类（CASH 产品 → 现金）；
 * - 派生缺失或字典未收录的行并入「其他」伪大类兜底，不丢行。
 * 展示顺序与颜色由字典 sort_order 驱动；「在途」固定插现金后、「其他」垫底。
 */

export const IN_TRANSIT_CODES = new Set(["IN_TRANSIT_BUY", "IN_TRANSIT_SELL", "IN_TRANSIT_DIVIDEND"]);

/** 伪大类稳定键（非字典维度值，前端固定附加） */
export const PSEUDO_IN_TRANSIT_CODE = "__IN_TRANSIT__";
export const PSEUDO_OTHER_CODE = "__OTHER__";

export interface AllocationItem {
  /** 大类展示名（维度 name / 在途 / 其他） */
  key: string;
  /** 大类稳定键（维度 code / 伪大类键） */
  code: string;
  /** 市值合计（元） */
  value: number;
  /** 占比（%）：最大余数法，全部项加总恒为 100.0 */
  percent: number;
  color: string;
}

/** 持仓行金额：净值型取 market_value，现金/在途行取 cash_amount（快照中两者一致） */
export function positionAmount(p: Position): number {
  return p.market_value ?? p.cash_amount ?? 0;
}

/** 持仓行 → 大类稳定键：在途 > asset_class_code > 其他 */
export function categoryCodeOf(p: Position): string {
  if (IN_TRANSIT_CODES.has(p.product_code)) return PSEUDO_IN_TRANSIT_CODE;
  return p.asset_class_code || PSEUDO_OTHER_CODE;
}

/**
 * 行级占比（最大余数法，与传入 positions 顺序对齐）。
 * 全部行加总恒为 100.0%；分区/图例/卡片均由它加总，杜绝 ±0.1% 漂移。
 */
export function buildRowPercents(positions: Position[]): number[] {
  return largestRemainderPercents(positions.map((p) => positionAmount(p)));
}

/**
 * 按资产大类聚合持仓（大类占比 = 行级占比加总，与分区头/卡片严格自洽）。
 * assetClasses：asset_class 维度字典（入参顺序不限，内部按 sort_order 排序）。
 */
export function buildAllocation(
  positions: Position[],
  assetClasses: AssetClassificationItem[]
): AllocationItem[] {
  const rowPercents = buildRowPercents(positions);
  const byCode = new Map<string, { value: number; percent: number }>();
  positions.forEach((p, i) => {
    const code = categoryCodeOf(p);
    const cur = byCode.get(code) ?? { value: 0, percent: 0 };
    cur.value += positionAmount(p);
    cur.percent += rowPercents[i];
    byCode.set(code, cur);
  });

  const items: AllocationItem[] = [];
  const emitted = new Set<string>();

  // 在途伪大类：先算好，固定插到现金大类之后（无现金行则随字典序末尾追加）
  let inTransitItem: AllocationItem | null = null;
  const inTransitAgg = byCode.get(PSEUDO_IN_TRANSIT_CODE);
  if (inTransitAgg) {
    emitted.add(PSEUDO_IN_TRANSIT_CODE);
    inTransitItem = {
      key: "在途",
      code: PSEUDO_IN_TRANSIT_CODE,
      value: inTransitAgg.value,
      percent: inTransitAgg.percent,
      color: IN_TRANSIT_COLOR,
    };
  }

  for (const ac of [...assetClasses].sort((a, b) => a.sort_order - b.sort_order)) {
    const agg = byCode.get(ac.code);
    if (agg) {
      emitted.add(ac.code);
      items.push({
        key: ac.name,
        code: ac.code,
        value: agg.value,
        percent: agg.percent,
        color: assetClassColor(ac.sort_order),
      });
    }
    if (ac.code === "ASSET_CASH" && inTransitItem) {
      items.push(inTransitItem);
      inTransitItem = null;
    }
  }
  if (inTransitItem) items.push(inTransitItem);

  // 其他：显式兜底 code + 字典未收录 code 合并（不丢行），固定垫底
  let otherValue = 0;
  let otherPercent = 0;
  let hasOther = false;
  for (const [code, agg] of byCode) {
    if (emitted.has(code)) continue;
    hasOther = true;
    otherValue += agg.value;
    otherPercent += agg.percent;
  }
  if (hasOther) {
    items.push({
      key: "其他",
      code: PSEUDO_OTHER_CODE,
      value: otherValue,
      percent: otherPercent,
      color: OTHER_COLOR,
    });
  }
  return items;
}
