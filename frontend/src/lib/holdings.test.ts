import { describe, it, expect } from "vitest";
import {
  buildCardPercentMap,
  buildProductSections,
  productCardKey,
  sumGroupPercent,
  OTHER_SECTION_NAME,
  UNCATEGORIZED_GROUP_NAME,
} from "@/lib/holdings";
import { PSEUDO_IN_TRANSIT_CODE } from "@/lib/allocation";
import type { SubDimension } from "@/lib/dimensions";
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
  makeDictItem("ASSET_COMMODITY", "商品", 3),
  makeDictItem("ASSET_CASH", "现金", 4),
];

/** 完整四维股票夹具（#915：组合键断言必须四维非空，否则全是占位符无意义） */
function makeStockProduct(overrides: Partial<HoldingProductAggregate> = {}): HoldingProductAggregate {
  return makeProduct({
    style_code: "STYLE_BALANCED",
    style_name: "平衡",
    size_code: "SIZE_LARGE",
    size_name: "大盘",
    segment_code: "SEG_COMPOSITE",
    segment_name: "综合",
    ...overrides,
  });
}

/** #915 前的单维分组参照实现（等价断言用；按 name 分组、未分类恒垫底） */
function legacySingleDimGroups(items: HoldingProductAggregate[], dim: string) {
  const byName = new Map<string, HoldingProductAggregate[]>();
  for (const p of items) {
    const name = (p[`${dim}_name` as keyof HoldingProductAggregate] as string | null) ?? UNCATEGORIZED_GROUP_NAME;
    const arr = byName.get(name) ?? [];
    arr.push(p);
    byName.set(name, arr);
  }
  const groups = [...byName.entries()].map(([name, ps]) => {
    const sorted = [...ps].sort((a, b) => b.market_value - a.market_value);
    return { name, products: sorted, total: sorted.reduce((s, p) => s + p.market_value, 0) };
  });
  groups.sort((a, b) => {
    if (a.name === UNCATEGORIZED_GROUP_NAME) return 1;
    if (b.name === UNCATEGORIZED_GROUP_NAME) return -1;
    return b.total - a.total;
  });
  return groups;
}

describe("buildProductSections", () => {
  it("按字典 sort_order 排序分区并跳过空大类", () => {
    const products = [
      makeProduct({ product_code: "B1", asset_class_code: "ASSET_BOND", asset_class_name: "债券", segment_code: "SEG_BOND_SHORT", segment_name: "短债", market_value: 50 }),
      makeStockProduct({ product_code: "S1", market_value: 200 }),
    ];
    const sections = buildProductSections(products, DICT);
    expect(sections.map((s) => s.name)).toEqual(["股票", "债券"]);
    expect(sections[0].total).toBe(200);
    expect(sections[1].total).toBe(50);
  });

  it("默认子集组合键（D3）：股票 region,segment 拼接「·」，组间合计降序、含占位符组恒垫底、组内市值降序", () => {
    const products = [
      makeStockProduct({ product_code: "A", market_value: 100 }),
      makeStockProduct({ product_code: "B", market_value: 300 }),
      makeStockProduct({ product_code: "C", segment_code: "SEG_DIVIDEND", segment_name: "红利", market_value: 200 }),
      makeStockProduct({ product_code: "D", region_code: "REGION_US", region_name: "美国", market_value: 50 }),
      makeStockProduct({ product_code: "E", segment_code: null, segment_name: null, market_value: 999 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.subDims).toEqual(["region", "segment"]);
    expect(section.groups.map((g) => g.name)).toEqual([
      "中国·综合",
      "中国·红利",
      "美国·综合",
      "中国·未分类", // 999 最大仍垫底（含占位符恒垫底，§6.3）
    ]);
    expect(section.groups[0].total).toBe(400);
    expect(section.groups[0].products.map((p) => p.product_code)).toEqual(["B", "A"]);
    expect(section.groups[3].hasPlaceholder).toBe(true);
    expect(section.total).toBe(1649);
  });

  it("债券默认 region,segment（D3 变更：原 segment 单维）", () => {
    const products = [
      makeProduct({ product_code: "B1", asset_class_code: "ASSET_BOND", asset_class_name: "债券", segment_code: "SEG_BOND_SHORT", segment_name: "短债", market_value: 100 }),
      makeProduct({ product_code: "B2", asset_class_code: "ASSET_BOND", asset_class_name: "债券", region_code: "REGION_US", region_name: "美国", segment_code: "SEG_BOND_SHORT", segment_name: "短债", market_value: 50 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.subDims).toEqual(["region", "segment"]);
    expect(section.groups.map((g) => g.name)).toEqual(["中国·短债", "美国·短债"]);
  });

  it("现金平铺：subDims 空、组名与大类同名（组件据此不渲染 chip）", () => {
    const products = [
      makeProduct({
        product_code: "CASH",
        market: "",
        asset_class_code: "ASSET_CASH",
        asset_class_name: "现金",
        region_code: null,
        region_name: null,
        shares: null,
        cash_amount: 1000,
        market_value: 1000,
      }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.subDims).toEqual([]);
    expect(section.groups).toHaveLength(1);
    expect(section.groups[0].name).toBe("现金");
    expect(section.groups[0].hasPlaceholder).toBe(false);
  });

  it("商品 segment 单值；segment 为空归「未分类」组（不是平铺，与现金两条分支）", () => {
    const gold = makeProduct({ product_code: "G1", asset_class_code: "ASSET_COMMODITY", asset_class_name: "商品", region_code: null, region_name: null, segment_code: "SEG_GOLD", segment_name: "黄金", market_value: 100 });
    const noSeg = makeProduct({ product_code: "G2", asset_class_code: "ASSET_COMMODITY", asset_class_name: "商品", region_code: null, region_name: null, segment_code: null, segment_name: null, market_value: 50 });
    const section = buildProductSections([gold, noSeg], DICT).find((s) => s.code === "ASSET_COMMODITY")!;
    expect(section.subDims).toEqual(["segment"]);
    expect(section.groups.map((g) => g.name)).toEqual(["黄金", UNCATEGORIZED_GROUP_NAME]);
    expect(section.groups[1].hasPlaceholder).toBe(true);
  });

  it("入选维全空 → 收敛为单个「未分类」（不重复占位符）", () => {
    const products = [
      makeStockProduct({ product_code: "A", region_code: null, region_name: null, segment_code: null, segment_name: null, market_value: 100 }),
      makeStockProduct({ product_code: "B", region_code: null, region_name: null, segment_code: null, segment_name: null, market_value: 50 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.groups).toHaveLength(1);
    expect(section.groups[0].name).toBe(UNCATEGORIZED_GROUP_NAME);
    expect(section.groups[0].total).toBe(150);
  });

  it("部分入选维为空 → 占位符进键内（中国·未分类 与 美国·未分类 分开）", () => {
    const products = [
      makeStockProduct({ product_code: "A", segment_code: null, segment_name: null, market_value: 100 }),
      makeStockProduct({ product_code: "B", region_code: "REGION_US", region_name: "美国", segment_code: null, segment_name: null, market_value: 50 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.groups.map((g) => g.name)).toEqual(["中国·未分类", "美国·未分类"]);
  });

  it.each(["region", "style", "size", "segment"])(
    "单元素子集 ≡ 现状单维分组（%s，逐项全等：组名序列/各组 total/组内顺序/section.total）",
    (dim) => {
      const products = [
        makeStockProduct({ product_code: "A", market_value: 100 }),
        makeStockProduct({ product_code: "B", style_code: "STYLE_VALUE", style_name: "价值", size_code: "SIZE_SMALL", size_name: "中小盘", segment_code: "SEG_DIVIDEND", segment_name: "红利", market_value: 300 }),
        makeStockProduct({ product_code: "C", region_code: "REGION_US", region_name: "美国", market_value: 200 }),
        makeStockProduct({ product_code: "D", region_code: null, region_name: null, style_code: null, style_name: null, size_code: null, size_name: null, segment_code: null, segment_name: null, market_value: 999 }),
      ];
      const [section] = buildProductSections(products, DICT, { ASSET_STOCK: dim });
      const legacy = legacySingleDimGroups(products, dim);
      expect(section.groups.map((g) => g.name)).toEqual(legacy.map((g) => g.name));
      expect(section.groups.map((g) => g.total)).toEqual(legacy.map((g) => g.total));
      expect(section.groups.map((g) => g.products.map((p) => p.product_code))).toEqual(
        legacy.map((g) => g.products.map((p) => p.product_code))
      );
      expect(section.total).toBe(legacy.reduce((s, g) => s + g.total, 0));
    }
  );

  it("一卡恰属一组守卫：各组产品数之和=输入数、任一卡键只出现一次、section.total=大类全部卡市值之和", () => {
    const products = [
      makeStockProduct({ product_code: "A", market_value: 100 }),
      makeStockProduct({ product_code: "B", segment_code: "SEG_DIVIDEND", segment_name: "红利", market_value: 300 }),
      makeStockProduct({ product_code: "C", region_code: "REGION_US", region_name: "美国", market_value: 200 }),
      makeStockProduct({ product_code: "D", segment_code: null, segment_name: null, market_value: 999 }),
      makeStockProduct({ product_code: "E", region_code: null, region_name: null, segment_code: null, segment_name: null, market_value: 5 }),
    ];
    const [section] = buildProductSections(products, DICT);
    const all = section.groups.flatMap((g) => g.products);
    expect(all).toHaveLength(products.length);
    expect(new Set(all.map(productCardKey)).size).toBe(products.length);
    expect(section.total).toBe(products.reduce((s, p) => s + p.market_value, 0));
  });

  it("按 code 分组、按 name 显示：同维度同名不同 code 分成两组、codeKey 不重复", () => {
    const products = [
      makeStockProduct({ product_code: "A", market_value: 100 }),
      makeStockProduct({ product_code: "B", region_code: "REGION_CN_DUP", region_name: "中国", market_value: 50 }),
    ];
    const [section] = buildProductSections(products, DICT, { ASSET_STOCK: "region" });
    expect(section.groups).toHaveLength(2);
    expect(section.groups.map((g) => g.name)).toEqual(["中国", "中国"]);
    expect(new Set(section.groups.map((g) => g.codeKey)).size).toBe(2);
  });

  it("display_config 多值子集覆盖；顺序无语义（segment,region ≡ region,segment）", () => {
    const products = [
      makeStockProduct({ product_code: "A", market_value: 100 }),
      makeStockProduct({ product_code: "B", style_code: "STYLE_VALUE", style_name: "价值", market_value: 300 }),
    ];
    const a = buildProductSections(products, DICT, { ASSET_STOCK: "region,style" });
    const b = buildProductSections(products, DICT, { ASSET_STOCK: "style,region" });
    expect(a[0].subDims).toEqual(["region", "style"]);
    expect(a[0].groups.map((g) => g.name)).toEqual(["中国·价值", "中国·平衡"]);
    expect(b[0].groups.map((g) => g.name)).toEqual(a[0].groups.map((g) => g.name));
    expect(b[0].groups.map((g) => g.total)).toEqual(a[0].groups.map((g) => g.total));
  });

  it("读侧脏值防御回退默认子集（不抛错）", () => {
    const products = [makeStockProduct({ product_code: "A" })];
    const [section] = buildProductSections(products, DICT, { ASSET_STOCK: "bogus" });
    expect(section.subDims).toEqual(["region", "segment"]);
    expect(section.groups[0].name).toBe("中国·综合");
  });

  it("asset_class 不在字典或缺失 → 其他桶垫底平铺", () => {
    const products = [
      makeStockProduct({ product_code: "S1", market_value: 100 }),
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

  it("占比独立期望（组合键下）：行级/逐组 chip/分区头均为手工推算写死值；末尾保留自洽总式", () => {
    const products = [
      makeStockProduct({ product_code: "A", market_value: 100 }),
      makeStockProduct({ product_code: "B", segment_code: "SEG_DIVIDEND", segment_name: "红利", market_value: 300 }),
      makeStockProduct({ product_code: "C", region_code: "REGION_US", region_name: "美国", market_value: 200 }),
      makeProduct({ product_code: "CASH", market: "", asset_class_code: "ASSET_CASH", asset_class_name: "现金", region_code: null, region_name: null, shares: null, cash_amount: 400, market_value: 400 }),
    ];
    const inTransit = 100;
    const map = buildCardPercentMap(products, inTransit);
    const percentOf = (p: HoldingProductAggregate) => map.get(productCardKey(p)) ?? 0;
    // 手工最大余数法（总额 1100）：千分位 floor 90/272/181/363/90、余 4 席按余数
    // 降序给 A/在途/C/B → 行级 9.1/27.3/18.2/36.3/9.1（写死，独立于分组实现）
    expect(map.get(productCardKey(products[0]))).toBe(9.1);
    expect(map.get(productCardKey(products[1]))).toBe(27.3);
    expect(map.get(productCardKey(products[2]))).toBe(18.2);
    expect(map.get(productCardKey(products[3]))).toBe(36.3);
    expect(map.get(PSEUDO_IN_TRANSIT_CODE)).toBe(9.1);

    const sections = buildProductSections(products, DICT);
    const [stock, cash] = sections;
    expect(stock.groups.map((g) => g.name)).toEqual(["中国·红利", "美国·综合", "中国·综合"]);
    // 逐组 chip 合计 = 组内行级写死值之和（改任一组归属即独立变红）
    const expectedChip = [27.3, 18.2, 9.1];
    stock.groups.forEach((g, i) => {
      expect(sumGroupPercent(g.products, percentOf)).toBeCloseTo(expectedChip[i], 5);
    });
    // 分区头 = 名下全部行级写死值之和（54.6 / 36.3），不再与 chip 同集合互算
    expect(sumGroupPercent(stock.groups.flatMap((g) => g.products), percentOf)).toBeCloseTo(54.6, 5);
    expect(sumGroupPercent(cash.groups.flatMap((g) => g.products), percentOf)).toBeCloseTo(36.3, 5);
    // 自洽总式保留：分区头加总 + 在途 = 100.0
    const sectionPercentSum = sections.reduce(
      (s, sec) => s + sumGroupPercent(sec.groups.flatMap((g) => g.products), percentOf),
      0
    );
    expect(sectionPercentSum + (map.get(PSEUDO_IN_TRANSIT_CODE) ?? 0)).toBeCloseTo(100.0, 5);
  });

  it("code 有值/name 为空（字典行缺失脏数据）：占位判据与显示层对齐，含空 name 组恒垫底", () => {
    const products = [
      makeStockProduct({ product_code: "A", segment_code: "SEG_DIVIDEND", segment_name: null, market_value: 999 }),
      makeStockProduct({ product_code: "B", market_value: 100 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.groups.map((g) => g.name)).toEqual(["中国·综合", "中国·未分类"]);
    expect(section.groups[1].hasPlaceholder).toBe(true);
    expect(section.groups[1].total).toBe(999); // 最大仍垫底
  });

  it("code 全有值但 name 全空 → 收敛单个「未分类」（不重复占位符）且垫底", () => {
    const products = [
      makeStockProduct({ product_code: "A", segment_code: "SEG_DIVIDEND", region_name: null, segment_name: null, market_value: 100 }),
      makeStockProduct({ product_code: "B", segment_code: "SEG_DIVIDEND", region_name: null, segment_name: null, market_value: 50 }),
      makeStockProduct({ product_code: "C", market_value: 10 }),
    ];
    const [section] = buildProductSections(products, DICT);
    expect(section.groups.map((g) => g.name)).toEqual(["中国·综合", UNCATEGORIZED_GROUP_NAME]);
    expect(section.groups[1].hasPlaceholder).toBe(true);
    expect(section.groups[1].total).toBe(150);
  });

  it("空输入返回空数组", () => {
    expect(buildProductSections([], DICT)).toEqual([]);
  });
});

describe("buildProductSections 页面级临时子集覆盖（#915 B2）", () => {
  /** 种子规则矩阵形态（backend/app/constants/asset_dimensions.py::DIMENSION_RULES） */
  const RULES = {
    ASSET_STOCK: { region: "required", style: "required", size: "required", segment: "optional" },
    ASSET_BOND: { region: "required", segment: "required" },
    ASSET_COMMODITY: { segment: "optional" },
  };

  const products = [
    makeStockProduct({ product_code: "A", market_value: 100 }),
    makeStockProduct({ product_code: "B", style_code: "STYLE_VALUE", style_name: "价值", market_value: 300 }),
    makeStockProduct({ product_code: "C", size_code: "SIZE_SMALL", size_name: "中小盘", market_value: 200 }),
    makeProduct({ product_code: "B1", asset_class_code: "ASSET_BOND", asset_class_name: "债券", segment_code: "SEG_BOND_SHORT", segment_name: "短债", market_value: 50 }),
  ];

  it("单维覆盖 ≡ 现状单维分组（B2 快捷切换的承重等价，四维各测一次）", () => {
    const stocks = products.filter((p) => p.asset_class_code === "ASSET_STOCK");
    for (const dim of ["region", "style", "size", "segment"] as const) {
      const [section] = buildProductSections(products, DICT, null, RULES, { ASSET_STOCK: [dim] });
      expect(section.subDims).toEqual([dim]);
      const legacy = legacySingleDimGroups(stocks, dim);
      expect(section.groups.map((g) => g.name)).toEqual(legacy.map((g) => g.name));
      expect(section.groups.map((g) => g.total)).toEqual(legacy.map((g) => g.total));
      expect(section.groups.map((g) => g.products.map((p) => p.product_code))).toEqual(
        legacy.map((g) => g.products.map((p) => p.product_code))
      );
    }
  });

  it("覆盖优先于显式配置与默认子集；仅作用于该大类（债券不受股票覆盖影响）", () => {
    const [stock, bond] = buildProductSections(products, DICT, { ASSET_STOCK: "style" }, RULES, {
      ASSET_STOCK: ["size"],
    });
    expect(stock.subDims).toEqual(["size"]);
    expect(stock.groups.map((g) => g.name)).toEqual(["大盘", "中小盘"]);
    expect(bond.subDims).toEqual(["region", "segment"]);
    expect(bond.groups[0].name).toBe("中国·短债");
  });

  it("多维覆盖 = 直切子集组合键（风格·规模）", () => {
    const [section] = buildProductSections(products, DICT, null, RULES, {
      ASSET_STOCK: ["style", "size"],
    });
    expect(section.subDims).toEqual(["style", "size"]);
    expect(section.groups.map((g) => g.name)).toEqual(["价值·大盘", "平衡·中小盘", "平衡·大盘"]);
  });

  it("覆盖不改合计口径：section.total 与一卡恰属一组守卫在有/无覆盖下全等", () => {
    const base = buildProductSections(products, DICT, null, RULES);
    const switched = buildProductSections(products, DICT, null, RULES, {
      ASSET_STOCK: ["style", "size"],
    });
    expect(switched.map((s) => s.total)).toEqual(base.map((s) => s.total));
    const all = switched[0].groups.flatMap((g) => g.products);
    expect(all).toHaveLength(3);
    expect(new Set(all.map(productCardKey)).size).toBe(3);
  });

  it("覆盖含规则未登记维 → 整条失效回退配置/默认（读侧防御同语义）；现金覆盖恒失效保持平铺", () => {
    const [stock, bond] = buildProductSections(products, DICT, { ASSET_STOCK: "style" }, RULES, {
      ASSET_STOCK: ["style", "bogus" as SubDimension],
      ASSET_BOND: ["style"],
    });
    expect(stock.subDims).toEqual(["style"]); // 回退显式配置
    expect(bond.subDims).toEqual(["region", "segment"]); // 回退默认
    const cash = makeProduct({ product_code: "CASH", market: "", asset_class_code: "ASSET_CASH", asset_class_name: "现金", region_code: null, region_name: null, cash_amount: 1000, market_value: 1000 });
    const [cashSection] = buildProductSections([cash], DICT, null, RULES, { ASSET_CASH: ["segment"] });
    expect(cashSection.subDims).toEqual([]);
    expect(cashSection.groups[0].name).toBe("现金");
  });

  it("其他桶不受覆盖影响（恒平铺垫底）", () => {
    const withOther = [
      ...products,
      makeProduct({ product_code: "X1", asset_class_code: "ASSET_UNKNOWN", market_value: 50 }),
    ];
    const sections = buildProductSections(withOther, DICT, null, RULES, { ASSET_UNKNOWN: ["region"] });
    const other = sections[sections.length - 1];
    expect(other.name).toBe(OTHER_SECTION_NAME);
    expect(other.subDims).toEqual([]);
    expect(other.groups[0].name).toBe(OTHER_SECTION_NAME);
  });
});

describe("sumGroupPercent", () => {
  it("按行级占比求和（percentOf 注入，分区头取整由展示层负责）", () => {
    const products = [makeProduct({}), makeProduct({}), makeProduct({})];
    const percentOf = () => 33.4;
    expect(sumGroupPercent(products, percentOf)).toBeCloseTo(100.2, 5);
  });
});

describe("buildCardPercentMap", () => {
  it("三行等值 → 33.4/33.3/33.3，加总恒 100.0（§4 反例）", () => {
    const products = [
      makeProduct({ product_code: "A", market_value: 100 }),
      makeProduct({ product_code: "B", market_value: 100 }),
      makeProduct({ product_code: "C", market_value: 100 }),
    ];
    const map = buildCardPercentMap(products, 0);
    expect(map.get(productCardKey(products[0]))).toBe(33.4);
    expect([...map.values()].reduce((s, v) => s + v, 0)).toBeCloseTo(100.0, 5);
    expect(new Set(map.values()).size).toBe(2); // 33.4 + 33.3 + 33.3
  });

  it("在途 >0 时参与同一行集，键为 PSEUDO_IN_TRANSIT_CODE；=0 时不产生在途行", () => {
    const products = [makeProduct({ product_code: "A", market_value: 300 })];
    const withTransit = buildCardPercentMap(products, 100);
    expect(withTransit.get(PSEUDO_IN_TRANSIT_CODE)).toBe(25.0);
    expect(withTransit.get(productCardKey(products[0]))).toBe(75.0);
    const withoutTransit = buildCardPercentMap(products, 0);
    expect(withoutTransit.has(PSEUDO_IN_TRANSIT_CODE)).toBe(false);
    expect(withoutTransit.get(productCardKey(products[0]))).toBe(100.0);
  });

  it("空行集返回空 Map", () => {
    expect(buildCardPercentMap([], 0).size).toBe(0);
  });
});