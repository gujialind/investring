import { describe, it, expect } from "vitest";
import {
  resolveSubDims,
  parseDisplaySubset,
  filterRegisteredDims,
  parseSubDimOverrides,
  serializeSubDimOverrides,
  toggleSubDimOverride,
  resolveViewSubDims,
  getDimensionOptions,
  clearInapplicableDims,
  DEFAULT_SUB_DIMS_BY_CLASS,
} from "@/lib/dimensions";
import type { AssetClassificationItem } from "@/types/asset-classification";

function item(over: Partial<AssetClassificationItem>): AssetClassificationItem {
  return {
    code: "X",
    dimension: "region",
    name: "x",
    sort_order: 1,
    is_active: true,
    applicable_asset_classes: [],
    ...over,
  };
}

/** 种子规则矩阵形态（backend/app/constants/asset_dimensions.py::DIMENSION_RULES） */
const RULES = {
  ASSET_STOCK: { region: "required", style: "required", size: "required", segment: "optional" },
  ASSET_BOND: { region: "required", segment: "required" },
  ASSET_COMMODITY: { segment: "optional" },
};

describe("默认子集表（#915 D3/D4）", () => {
  it("与文档口径一致（backend/AGENTS.md §1.4）：股票/债券 region,segment、商品 segment、现金平铺", () => {
    expect(DEFAULT_SUB_DIMS_BY_CLASS).toEqual({
      ASSET_STOCK: ["region", "segment"],
      ASSET_BOND: ["region", "segment"],
      ASSET_COMMODITY: ["segment"],
      ASSET_CASH: [],
    });
  });

  it("默认子集恒为该大类非禁止维度的子集（规则矩阵种子）", () => {
    for (const [code, dims] of Object.entries(DEFAULT_SUB_DIMS_BY_CLASS)) {
      const allowed = Object.keys(RULES[code as keyof typeof RULES] ?? {});
      for (const d of dims) expect(allowed).toContain(d);
    }
  });
});

describe("resolveSubDims", () => {
  it("组合级显式子集优先，按全序返回", () => {
    expect(resolveSubDims({ ASSET_STOCK: "style" }, "ASSET_STOCK")).toEqual(["style"]);
    expect(resolveSubDims({ ASSET_STOCK: "region,segment" }, "ASSET_STOCK")).toEqual([
      "region",
      "segment",
    ]);
  });

  it("子集顺序无语义：乱序串归一为固定全序 region→style→size→segment", () => {
    expect(resolveSubDims({ ASSET_STOCK: "segment,region" }, "ASSET_STOCK")).toEqual([
      "region",
      "segment",
    ]);
    expect(
      resolveSubDims({ ASSET_STOCK: "segment,size,style,region" }, "ASSET_STOCK")
    ).toEqual(["region", "style", "size", "segment"]);
  });

  it("拆分项 strip；单值 = 单元素子集（存量兼容）", () => {
    expect(resolveSubDims({ ASSET_STOCK: " region , segment " }, "ASSET_STOCK")).toEqual([
      "region",
      "segment",
    ]);
    expect(resolveSubDims({ ASSET_BOND: "segment" }, "ASSET_BOND")).toEqual(["segment"]);
  });

  it("无覆盖走默认子集表；现金平铺；未知大类空子集", () => {
    expect(resolveSubDims(null, "ASSET_STOCK")).toEqual(["region", "segment"]);
    expect(resolveSubDims(undefined, "ASSET_BOND")).toEqual(["region", "segment"]);
    expect(resolveSubDims({}, "ASSET_COMMODITY")).toEqual(["segment"]);
    expect(resolveSubDims({}, "ASSET_CASH")).toEqual([]);
    expect(resolveSubDims(null, "ASSET_UNKNOWN")).toEqual([]);
  });

  it("非法值防御回退默认子集（空串/纯空白/未知维度/重复项/空拆分项）", () => {
    const def = ["region", "segment"];
    for (const bad of ["", "   ", "bogus", "region,bogus", "region,region", "region,", "region,,segment"]) {
      expect(resolveSubDims({ ASSET_STOCK: bad }, "ASSET_STOCK")).toEqual(def);
    }
  });

  it("传规则矩阵时：未登记维度整串回退默认（规则事后收紧的防御）", () => {
    expect(resolveSubDims({ ASSET_BOND: "region,style" }, "ASSET_BOND", RULES)).toEqual([
      "region",
      "segment",
    ]);
    expect(resolveSubDims({ ASSET_CASH: "segment" }, "ASSET_CASH", RULES)).toEqual([]);
    // 合法子集不受影响
    expect(resolveSubDims({ ASSET_BOND: "segment,region" }, "ASSET_BOND", RULES)).toEqual([
      "region",
      "segment",
    ]);
  });

  it("传规则矩阵时：默认子集也按登记过滤（默认含被收紧维度时剔除）", () => {
    const tightened = { ASSET_STOCK: { region: "required" } };
    expect(resolveSubDims(null, "ASSET_STOCK", tightened)).toEqual(["region"]);
  });
});

describe("parseDisplaySubset（弹窗回填形态解析）", () => {
  it("拆分/strip/去重/全序归一", () => {
    expect(parseDisplaySubset("segment, region ,segment")).toEqual(["region", "segment"]);
    expect(parseDisplaySubset("style")).toEqual(["style"]);
  });

  it("空值与非法项：空串/null/缺省 → 空数组；未知维度剔除（尽力回显合法部分）", () => {
    expect(parseDisplaySubset("")).toEqual([]);
    expect(parseDisplaySubset(null)).toEqual([]);
    expect(parseDisplaySubset(undefined)).toEqual([]);
    expect(parseDisplaySubset("bogus,region")).toEqual(["region"]);
  });
});

describe("filterRegisteredDims（弹窗回填/提交登记过滤，L2 r1）", () => {
  it("仅保留已登记维度、保持全序；与 resolveSubDims 同一登记判据", () => {
    expect(filterRegisteredDims(["region", "style"], "ASSET_STOCK", RULES)).toEqual([
      "region",
      "style",
    ]);
    const tightened = { ASSET_STOCK: { region: "required", segment: "optional" } };
    expect(filterRegisteredDims(["region", "style"], "ASSET_STOCK", tightened)).toEqual([
      "region",
    ]);
    expect(filterRegisteredDims(["style"], "ASSET_STOCK", tightened)).toEqual([]);
  });

  it("大类无规则行（现金）→ 全过滤空；不传 rules → 原样返回", () => {
    expect(filterRegisteredDims(["segment"], "ASSET_CASH", RULES)).toEqual([]);
    expect(filterRegisteredDims(["style", "region"], "ASSET_STOCK")).toEqual([
      "style",
      "region",
    ]);
    expect(filterRegisteredDims(["style"], "ASSET_STOCK", null)).toEqual(["style"]);
  });
});

describe("parseSubDimOverrides（#915 B2 URL ?sub= 形态解析）", () => {
  it("null/空串 → 空覆盖；单条目与多条目解析", () => {
    expect(parseSubDimOverrides(null)).toEqual({});
    expect(parseSubDimOverrides("")).toEqual({});
    expect(parseSubDimOverrides("ASSET_STOCK.style")).toEqual({ ASSET_STOCK: ["style"] });
    expect(parseSubDimOverrides("ASSET_STOCK.style.size~ASSET_BOND.region")).toEqual({
      ASSET_STOCK: ["style", "size"],
      ASSET_BOND: ["region"],
    });
  });

  it("维度去重 + 全序归一（顺序无语义，与配置串同契约）", () => {
    expect(parseSubDimOverrides("ASSET_STOCK.segment.style.segment")).toEqual({
      ASSET_STOCK: ["style", "segment"],
    });
  });

  it("未知维度剔除（尽力保留合法部分）；无有效维度/空大类 code 的条目整条丢弃", () => {
    expect(parseSubDimOverrides("ASSET_STOCK.style.bogus")).toEqual({ ASSET_STOCK: ["style"] });
    expect(parseSubDimOverrides("ASSET_STOCK.bogus")).toEqual({});
    expect(parseSubDimOverrides("ASSET_STOCK")).toEqual({});
    expect(parseSubDimOverrides(".style~ASSET_BOND.region")).toEqual({ ASSET_BOND: ["region"] });
    expect(parseSubDimOverrides("~")).toEqual({});
  });

  it("与 serialize 往返全等（解析→序列化→解析幂等）", () => {
    const raw = "ASSET_BOND.region.segment~ASSET_STOCK.style";
    expect(serializeSubDimOverrides(parseSubDimOverrides(raw))).toBe(raw);
  });
});

describe("serializeSubDimOverrides（#915 B2 URL 写入）", () => {
  it("空覆盖/全空子集 → null（调用方移除参数，回默认）", () => {
    expect(serializeSubDimOverrides({})).toBeNull();
    expect(serializeSubDimOverrides({ ASSET_STOCK: [] })).toBeNull();
  });

  it("大类 code 字典序 + 维度全序：同一覆盖集恒产出同一串", () => {
    expect(
      serializeSubDimOverrides({
        ASSET_STOCK: ["segment", "region"],
        ASSET_BOND: ["region"],
      })
    ).toBe("ASSET_BOND.region~ASSET_STOCK.region.segment");
  });
});

describe("toggleSubDimOverride（#915 B2 快捷切换 + 直切子集同一交互）", () => {
  it("默认态首击 = 快捷切换到该单维（不是在默认子集上追加）", () => {
    expect(toggleSubDimOverride({}, "ASSET_STOCK", "style")).toEqual({ ASSET_STOCK: ["style"] });
  });

  it("覆盖态点击为勾选/取消，多选成子集、恒按全序归一", () => {
    const one = toggleSubDimOverride({}, "ASSET_STOCK", "segment");
    const two = toggleSubDimOverride(one, "ASSET_STOCK", "region");
    expect(two).toEqual({ ASSET_STOCK: ["region", "segment"] });
    expect(toggleSubDimOverride(two, "ASSET_STOCK", "region")).toEqual({
      ASSET_STOCK: ["segment"],
    });
  });

  it("取消最后一维 = 移除该大类覆盖、回默认（Q8 不支持强制平铺）", () => {
    const one = toggleSubDimOverride({}, "ASSET_STOCK", "style");
    expect(toggleSubDimOverride(one, "ASSET_STOCK", "style")).toEqual({});
  });

  it("其他大类覆盖保留；不改入参（纯函数）", () => {
    const base = { ASSET_BOND: ["region" as const] };
    const next = toggleSubDimOverride(base, "ASSET_STOCK", "size");
    expect(next).toEqual({ ASSET_BOND: ["region"], ASSET_STOCK: ["size"] });
    expect(base).toEqual({ ASSET_BOND: ["region"] });
  });
});

describe("resolveViewSubDims（#915 B2 消费侧：临时覆盖优先于配置/默认）", () => {
  it("不传/空覆盖时与 resolveSubDims 全等（B1 行为零变化）", () => {
    for (const overrides of [undefined, null, {}]) {
      expect(resolveViewSubDims(null, "ASSET_STOCK", RULES, overrides)).toEqual(
        resolveSubDims(null, "ASSET_STOCK", RULES)
      );
      expect(
        resolveViewSubDims({ ASSET_STOCK: "style" }, "ASSET_STOCK", RULES, overrides)
      ).toEqual(["style"]);
    }
    expect(resolveViewSubDims(null, "ASSET_STOCK", RULES, { ASSET_STOCK: [] })).toEqual([
      "region",
      "segment",
    ]);
  });

  it("覆盖优先于显式配置与默认子集表；仅作用于该大类", () => {
    expect(
      resolveViewSubDims({ ASSET_STOCK: "style" }, "ASSET_STOCK", RULES, {
        ASSET_STOCK: ["size"],
      })
    ).toEqual(["size"]);
    expect(resolveViewSubDims(null, "ASSET_BOND", RULES, { ASSET_STOCK: ["size"] })).toEqual([
      "region",
      "segment",
    ]);
  });

  it("覆盖乱序归一为全序；单元素覆盖 = 单维分组口径", () => {
    expect(
      resolveViewSubDims(null, "ASSET_STOCK", RULES, { ASSET_STOCK: ["segment", "region"] })
    ).toEqual(["region", "segment"]);
    expect(
      resolveViewSubDims(null, "ASSET_STOCK", RULES, { ASSET_STOCK: ["style"] })
    ).toEqual(["style"]);
  });

  it("覆盖含规则未登记维 → 整条失效回退（与读侧脏配置整串回退同语义）", () => {
    // 规则收紧后覆盖含失效维 → 回退到显式配置（合法时）
    const tightened = { ASSET_STOCK: { region: "required", segment: "optional" } };
    expect(
      resolveViewSubDims({ ASSET_STOCK: "region" }, "ASSET_STOCK", tightened, {
        ASSET_STOCK: ["region", "style"],
      })
    ).toEqual(["region"]);
    expect(
      resolveViewSubDims({ ASSET_BOND: "region" }, "ASSET_BOND", RULES, {
        ASSET_BOND: ["region", "style"],
      })
    ).toEqual(["region"]);
    // 无配置时回退默认子集表
    expect(
      resolveViewSubDims(null, "ASSET_BOND", RULES, { ASSET_BOND: ["style"] })
    ).toEqual(["region", "segment"]);
    // 现金无规则行：任何覆盖都失效，保持平铺
    expect(resolveViewSubDims(null, "ASSET_CASH", RULES, { ASSET_CASH: ["segment"] })).toEqual([]);
  });

  it("不传规则矩阵时覆盖不做登记过滤（形态解析结果直接生效）", () => {
    expect(resolveViewSubDims(null, "ASSET_STOCK", null, { ASSET_STOCK: ["style"] })).toEqual([
      "style",
    ]);
  });
});

describe("getDimensionOptions", () => {
  const dict = [
    item({ code: "ASSET_STOCK", dimension: "asset_class", name: "股票" }),
    item({ code: "REGION_CN", dimension: "region", name: "中国", applicable_asset_classes: ["ASSET_STOCK"] }),
    item({ code: "REGION_US", dimension: "region", name: "美国", applicable_asset_classes: ["ASSET_STOCK"] }),
    item({ code: "REGION_JP", dimension: "region", name: "日本", applicable_asset_classes: ["ASSET_BOND"] }),
    item({ code: "REGION_OFF", dimension: "region", name: "停用", is_active: false, applicable_asset_classes: ["ASSET_STOCK"] }),
  ];

  it("按维度过滤且排除停用值", () => {
    expect(getDimensionOptions(dict, "region").map((i) => i.code)).toEqual([
      "REGION_CN",
      "REGION_US",
      "REGION_JP",
    ]);
  });

  it("选了大类则按适用关系收窄", () => {
    expect(getDimensionOptions(dict, "region", "ASSET_STOCK").map((i) => i.code)).toEqual([
      "REGION_CN",
      "REGION_US",
    ]);
    expect(getDimensionOptions(dict, "region", "ASSET_BOND").map((i) => i.code)).toEqual([
      "REGION_JP",
    ]);
  });

  it("asset_class 维度入口：不传大类即启用大类列表", () => {
    expect(getDimensionOptions(dict, "asset_class").map((i) => i.code)).toEqual(["ASSET_STOCK"]);
  });
});

describe("clearInapplicableDims", () => {
  const dict = [
    item({ code: "REGION_CN", dimension: "region", applicable_asset_classes: ["ASSET_STOCK"] }),
    item({ code: "STYLE_GROWTH", dimension: "style", applicable_asset_classes: ["ASSET_BOND"] }),
    item({ code: "STYLE_OFF", dimension: "style", is_active: false, applicable_asset_classes: ["ASSET_STOCK"] }),
  ];

  it("不再适用新大类的维度值清空，适用的保留", () => {
    const next = clearInapplicableDims(
      { asset_class: undefined, region: "REGION_CN", style: "STYLE_GROWTH" },
      "ASSET_STOCK",
      dict
    );
    expect(next).toEqual({ asset_class: "ASSET_STOCK", region: "REGION_CN", style: undefined });
  });

  it("停用值视为不适用", () => {
    const next = clearInapplicableDims({ style: "STYLE_OFF" }, "ASSET_STOCK", dict);
    expect(next.style).toBeUndefined();
  });

  it("未选大类时仅更新 asset_class，其余维度不动", () => {
    const next = clearInapplicableDims({ region: "REGION_CN" }, undefined, dict);
    expect(next).toEqual({ asset_class: undefined, region: "REGION_CN" });
  });
});
