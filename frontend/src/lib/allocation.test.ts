import { describe, it, expect } from "vitest";
import {
  positionAmount,
  categoryCodeOf,
  buildRowPercents,
  buildAllocation,
  PSEUDO_IN_TRANSIT_CODE,
  PSEUDO_OTHER_CODE,
} from "@/lib/allocation";
import { assetClassColor, IN_TRANSIT_COLOR, OTHER_COLOR } from "@/lib/colors";
import type { Position } from "@/types/position";
import type { AssetClassificationItem } from "@/types/asset-classification";

let nextId = 1;
function makePosition(over: Partial<Position>): Position {
  return {
    id: nextId++,
    portfolio_code: "P1",
    product_code: "F1",
    snapshot_date: "2026-08-01",
    ...over,
  };
}

function ac(code: string, name: string, sortOrder: number): AssetClassificationItem {
  return {
    code,
    dimension: "asset_class",
    name,
    sort_order: sortOrder,
    is_active: true,
    applicable_asset_classes: [],
  };
}

const DICT = [
  ac("ASSET_STOCK", "股票", 1),
  ac("ASSET_BOND", "债券", 2),
  ac("ASSET_CASH", "现金", 4),
];

describe("positionAmount", () => {
  it("净值型取 market_value，现金/在途取 cash_amount，皆无取 0", () => {
    expect(positionAmount(makePosition({ market_value: 100, cash_amount: 50 }))).toBe(100);
    expect(positionAmount(makePosition({ market_value: undefined, cash_amount: 50 }))).toBe(50);
    expect(positionAmount(makePosition({}))).toBe(0);
  });
});

describe("categoryCodeOf", () => {
  it("在途 > asset_class_code > 其他", () => {
    expect(
      categoryCodeOf(makePosition({ product_code: "IN_TRANSIT_BUY", asset_class_code: "ASSET_CASH" }))
    ).toBe(PSEUDO_IN_TRANSIT_CODE);
    expect(
      categoryCodeOf(makePosition({ product_code: "IN_TRANSIT_SELL" }))
    ).toBe(PSEUDO_IN_TRANSIT_CODE);
    expect(
      categoryCodeOf(makePosition({ product_code: "IN_TRANSIT_DIVIDEND", asset_class_code: "ASSET_CASH" }))
    ).toBe(PSEUDO_IN_TRANSIT_CODE);
    expect(categoryCodeOf(makePosition({ asset_class_code: "ASSET_STOCK" }))).toBe("ASSET_STOCK");
    expect(categoryCodeOf(makePosition({ asset_class_code: null }))).toBe(PSEUDO_OTHER_CODE);
    expect(categoryCodeOf(makePosition({}))).toBe(PSEUDO_OTHER_CODE);
  });
});

describe("buildRowPercents", () => {
  it("行级占比加总恒 100.0", () => {
    const positions = [600, 400, 300, 200, 100].map((v) => makePosition({ market_value: v }));
    const percents = buildRowPercents(positions);
    expect(percents.reduce((s, p) => s + p, 0)).toBeCloseTo(100, 6);
  });

  it("空持仓返回空数组", () => {
    expect(buildRowPercents([])).toEqual([]);
  });
});

describe("buildAllocation", () => {
  it("按大类聚合：字典 sort_order 排序、在途插现金后、其他垫底，占比与行级自洽", () => {
    const positions = [
      makePosition({ product_code: "F1", market_value: 600, asset_class_code: "ASSET_STOCK" }),
      makePosition({ product_code: "F2", market_value: 400, asset_class_code: "ASSET_STOCK" }),
      makePosition({ product_code: "CASH", cash_amount: 300, asset_class_code: "ASSET_CASH" }),
      makePosition({ product_code: "IN_TRANSIT_BUY", cash_amount: 200 }),
      makePosition({ product_code: "F3", market_value: 100, asset_class_code: null }),
    ];
    const items = buildAllocation(positions, DICT);

    expect(items.map((i) => i.key)).toEqual(["股票", "现金", "在途", "其他"]);
    expect(items.map((i) => i.code)).toEqual([
      "ASSET_STOCK",
      "ASSET_CASH",
      PSEUDO_IN_TRANSIT_CODE,
      PSEUDO_OTHER_CODE,
    ]);
    expect(items.map((i) => i.value)).toEqual([1000, 300, 200, 100]);
    expect(items.map((i) => i.percent)).toEqual([62.5, 18.8, 12.5, 6.2]);
    expect(items.map((i) => i.color)).toEqual([
      assetClassColor(1),
      assetClassColor(4),
      IN_TRANSIT_COLOR,
      OTHER_COLOR,
    ]);
    // 无持仓的大类（债券）不占位
    expect(items.find((i) => i.code === "ASSET_BOND")).toBeUndefined();
  });

  it("分红与买卖在途合并，不并入现金或其他，占比保留全部金额", () => {
    const positions = [
      makePosition({ product_code: "CASH", cash_amount: 700, asset_class_code: "ASSET_CASH" }),
      makePosition({ product_code: "IN_TRANSIT_BUY", cash_amount: 100 }),
      makePosition({ product_code: "IN_TRANSIT_SELL", cash_amount: 150 }),
      makePosition({ product_code: "IN_TRANSIT_DIVIDEND", cash_amount: 50, asset_class_code: null }),
    ];
    const items = buildAllocation(positions, DICT);
    expect(items.map((i) => i.code)).toEqual(["ASSET_CASH", PSEUDO_IN_TRANSIT_CODE]);
    expect(items.map((i) => i.value)).toEqual([700, 300]);
    expect(items.map((i) => i.percent)).toEqual([70, 30]);
    expect(items[1].color).toBe(IN_TRANSIT_COLOR);
  });

  it("无现金大类时在途随字典序末尾追加", () => {
    const positions = [
      makePosition({ market_value: 800, asset_class_code: "ASSET_STOCK" }),
      makePosition({ product_code: "IN_TRANSIT_SELL", cash_amount: 200 }),
    ];
    const items = buildAllocation(positions, [ac("ASSET_STOCK", "股票", 1)]);
    expect(items.map((i) => i.key)).toEqual(["股票", "在途"]);
  });

  it("字典未收录的大类 code 并入「其他」不丢行", () => {
    const positions = [
      makePosition({ market_value: 700, asset_class_code: "ASSET_STOCK" }),
      makePosition({ market_value: 300, asset_class_code: "ASSET_FUTURE_NEW" }),
    ];
    const items = buildAllocation(positions, DICT);
    expect(items.map((i) => i.key)).toEqual(["股票", "其他"]);
    expect(items[1].value).toBe(300);
  });
});
