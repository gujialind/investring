import { describe, it, expect } from "vitest";
import {
  positionAmount,
  categoryCodeOf,
  buildRowPercents,
  buildAllocation,
  sortAllocationItems,
  PSEUDO_IN_TRANSIT_CODE,
  PSEUDO_OTHER_CODE,
  type AllocationItem,
} from "@/lib/allocation";
import {
  assetClassColor,
  DARK_CHART_COLORS,
  IN_TRANSIT_COLOR,
  OTHER_COLOR,
  otherColor,
} from "@/lib/colors";
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

describe("buildAllocation theme（#872 PR1，默认 light 行为不变）", () => {
  const positions = [
    makePosition({ market_value: 600, asset_class_code: "ASSET_STOCK" }),
    makePosition({ product_code: "CASH", cash_amount: 300, asset_class_code: "ASSET_CASH" }),
    makePosition({ product_code: "IN_TRANSIT_BUY", cash_amount: 200 }),
    makePosition({ market_value: 100, asset_class_code: null }),
  ];

  it("dark：大类/在途/其他全走暗色板，序位、金额与占比与 light 完全一致", () => {
    const light = buildAllocation(positions, DICT);
    const dark = buildAllocation(positions, DICT, "dark");
    expect(dark.map((i) => [i.key, i.code, i.value, i.percent])).toEqual(
      light.map((i) => [i.key, i.code, i.value, i.percent])
    );
    expect(dark.map((i) => i.color)).toEqual([
      DARK_CHART_COLORS[0], // 股票 C1
      DARK_CHART_COLORS[6], // 现金 C7
      DARK_CHART_COLORS[2], // 在途 C3
      DARK_CHART_COLORS[7], // 其他 C8
    ]);
  });

  it("显式 light 与默认调用结果全等", () => {
    expect(buildAllocation(positions, DICT, "light")).toEqual(buildAllocation(positions, DICT));
  });
});

describe("sortAllocationItems", () => {
  function item(code: string, value: number, percent: number, color = code): AllocationItem {
    return { code, key: code, value, percent, color };
  }

  it("按市值降序且不改输入与身份色，同值保持原顺序", () => {
    const items = [
      item("cash", 300, 30, assetClassColor(4)),
      item("stock", 600, 60, assetClassColor(1)),
      item("bond-a", 200, 10, assetClassColor(2)),
      item("bond-b", 200, 10, assetClassColor(2)),
    ];
    const original = structuredClone(items);

    const result = sortAllocationItems(items, "light");

    expect(result.map((entry) => entry.code)).toEqual(["stock", "cash", "bond-a", "bond-b"]);
    expect(result.map((entry) => entry.color)).toEqual([
      assetClassColor(1),
      assetClassColor(4),
      assetClassColor(2),
      assetClassColor(2),
    ]);
    expect(items).toEqual(original);
  });

  it("默认最多 6 类，尾部并入其他且金额与占比守恒", () => {
    const items = [
      item("a", 40, 22.2),
      item("b", 35, 19.5),
      item("c", 30, 16.7),
      item("d", 25, 13.9),
      item("e", 20, 11.1),
      item("f", 15, 8.3),
      item("g", 10, 5.6),
      item("h", 5, 2.7),
    ];

    const result = sortAllocationItems(items, "light");

    expect(result.map((entry) => entry.code)).toEqual([
      "a",
      "b",
      "c",
      PSEUDO_OTHER_CODE,
      "d",
      "e",
    ]);
    expect(result.map((entry) => entry.value)).toEqual([40, 35, 30, 30, 25, 20]);
    expect(result.map((entry) => entry.percent)).toEqual([22.2, 19.5, 16.7, 16.6, 13.9, 11.1]);
    expect(result[3].key).toBe("其他");
    expect(result[3].color).toBe(otherColor("light"));
    expect(result.reduce((sum, entry) => sum + entry.value, 0)).toBe(180);
    expect(result.reduce((sum, entry) => sum + entry.percent, 0)).toBeCloseTo(100, 6);
  });

  it("已有其他与尾部只合并一次，合并后仍按市值降序", () => {
    const items = [
      item(PSEUDO_OTHER_CODE, 100, 20, OTHER_COLOR),
      item("a", 90, 18),
      item("b", 80, 16),
      item("c", 70, 14),
      item("d", 60, 12),
      item("e", 50, 10),
      item("f", 40, 10),
    ];

    const result = sortAllocationItems(items, "dark");

    expect(result.map((entry) => entry.code)).toEqual([
      PSEUDO_OTHER_CODE,
      "a",
      "b",
      "c",
      "d",
      "e",
    ]);
    expect(result[0]).toMatchObject({ value: 140, percent: 30, color: otherColor("dark") });
    expect(result.filter((entry) => entry.code === PSEUDO_OTHER_CODE)).toHaveLength(1);
    expect(result.reduce((sum, entry) => sum + entry.value, 0)).toBe(490);
    expect(result.reduce((sum, entry) => sum + entry.percent, 0)).toBe(100);
  });

  it("恰好达到上限不合成其他，自定义上限生效", () => {
    const six = Array.from({ length: 6 }, (_, index) => item(`c${index}`, 60 - index * 10, 10));
    expect(sortAllocationItems(six, "light")).toHaveLength(6);
    expect(sortAllocationItems(six, "light").some((entry) => entry.code === PSEUDO_OTHER_CODE)).toBe(false);

    const result = sortAllocationItems(
      [item("a", 50, 50), item("b", 30, 30), item("c", 20, 20), item("d", 10, 10)],
      "light",
      3
    );
    expect(result.map((entry) => entry.code)).toEqual(["a", "b", PSEUDO_OTHER_CODE]);
    expect(result[2]).toMatchObject({ value: 30, percent: 30 });
  });

  it("空数组返回空数组", () => {
    expect(sortAllocationItems([], "light")).toEqual([]);
  });
});
