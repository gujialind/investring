import { describe, it, expect } from "vitest";
import {
  buildProductSections,
  sumGroupPercent,
  OTHER_SECTION_NAME,
  UNCATEGORIZED_GROUP_NAME,
} from "@/lib/holdings";
import type { HoldingProductAggregate } from "@/types/holding";
import type { AssetClassificationItem } from "@/types/asset-classification";

function makeProduct(overrides: Partial<HoldingProductAggregate>): HoldingProductAggregate {
  return {
    product_code: "P1",
    market: "CN_EXCHANGE",
    product_name: "产品",
    asset_class_code: "ASSET_STOCK",
    asset_class_name: "股票",
    region_code: "REGION_CN",
    region_name: "中国",
    style_code: null,
    style_name: null,
    size_code: null,
    size_name: null,
    segment_code: null,
    segment_name: null,
    market_value: 100,
    ratio: 0.1,
    shares: 10,
    cash_amount: null,
    holding_profit: 1,
    holding_profit_percent: 1,
    daily_profit: 0.5,
    cumulative_profit: 1,
    platforms: [],
    ...overrides,
  };
}

function makeDictItem(code: string, name: string, sortOrder: number): AssetClassificationItem {
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
  makeDictItem("ASSET_BOND", "债券", 2),
  makeDictItem("ASSET_STOCK", "股票", 1),
  makeDictItem("ASSET_CASH", "现金", 4),
];

describe("buildProductSections", () => {
  it("按字典 sort_order 排序分区并跳过空大类", () => {
    const products = [
      makeProduct({ product_code: "B1", asset_class_code: "ASSET_BOND", asset_class_name: "债券", market_value: 50 }),
      makeProduct({ product_code: "S1", market_value: 200 }),
    ];
    const sections = buildProductSections(products, DICT);
    expect(sections.map((s) => s.name)).toEqual(["股票", "债券"]);
    expect(sections[0].total).toBe(200);
    expect(sections[1].total).toBe(50);
  });

  it("股票按 region 二级分组：组间合计降序、未分类垫底、组内市值降序", () => {
    const products = [
      makeProduct({ product_code: "A", region_name: "中国", market_value: 100 }),
      makeProduct({ product_code: "B", region_name: "中国", market_value: 300 }),
      makeProduct({ product_code: "C", region_name: "中国香港", market_value: 200 }),
      makeProduct({ product_code: "D", region_name: null, market_value: 999 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.subDim).toBe("region");
    expect(section.groups.map((g) => g.name)).toEqual([
      "中国",
      "中国香港",
      UNCATEGORIZED_GROUP_NAME,
    ]);
    expect(section.groups[0].total).toBe(400);
    expect(section.groups[0].products.map((p) => p.product_code)).toEqual(["B", "A"]);
  });

  it("现金平铺：组名与大类同名（组件据此不渲染 chip）", () => {
    const products = [
      makeProduct({
        product_code: "CASH",
        market: "",
        asset_class_code: "ASSET_CASH",
        asset_class_name: "现金",
        region_name: null,
        shares: null,
        cash_amount: 1000,
        market_value: 1000,
      }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.subDim).toBeNull();
    expect(section.groups).toHaveLength(1);
    expect(section.groups[0].name).toBe("现金");
  });

  it("display_config 覆盖二级分组维度（issue #144）", () => {
    const products = [
      makeProduct({ product_code: "A", style_name: "平衡", market_value: 100 }),
    ];
    const [section] = buildProductSections(products, DICT, {
      ASSET_STOCK: "style",
    });
    expect(section.subDim).toBe("style");
    expect(section.groups.map((g) => g.name)).toEqual(["平衡"]);
  });

  it("asset_class 不在字典或缺失 → 其他桶垫底平铺", () => {
    const products = [
      makeProduct({ product_code: "S1", market_value: 100 }),
      makeProduct({ product_code: "X1", asset_class_code: "ASSET_UNKNOWN", market_value: 50 }),
      makeProduct({ product_code: "X2", asset_class_code: null, market_value: 30 }),
    ];
    const sections = buildProductSections(products, DICT);
    expect(sections.map((s) => s.name)).toEqual(["股票", OTHER_SECTION_NAME]);
    const other = sections[1];
    expect(other.sortOrder).toBeNull();
    expect(other.groups[0].name).toBe(OTHER_SECTION_NAME);
    expect(other.total).toBe(80);
  });

  it("空输入返回空数组", () => {
    expect(buildProductSections([], DICT)).toEqual([]);
  });
});

describe("sumGroupPercent", () => {
  it("按 ratio 求和转百分数，null 按 0 计", () => {
    const products = [
      makeProduct({ ratio: 0.224 }),
      makeProduct({ ratio: 0.187 }),
      makeProduct({ ratio: null }),
    ];
    expect(sumGroupPercent(products)).toBeCloseTo(41.1, 5);
  });
});
