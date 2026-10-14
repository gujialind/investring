import { describe, it, expect } from "vitest";
import { navBasisLabels } from "@/lib/navBasis";

// #703：后端在产品整段历史都没有累计净值时回退到单位净值口径（nav_basis="unit"），
// 数字仍从 accumulated_nav 字段返回。回退口径不含分红再投资、场内还是市价，标题必须
// 跟着变——照旧写「累计净值走势」等于把另一种收益标成累计净值。
describe("navBasisLabels", () => {
  it("accumulated 口径维持原标题且不加注记", () => {
    for (const market of ["CN_OTC", "HK_MUTUAL", "CN_EXCHANGE"]) {
      expect(navBasisLabels("accumulated", market)).toEqual({
        curveTitle: "累计净值走势",
        returnsTitle: "区间收益率",
        note: null,
      });
    }
  });

  it("首屏在途（basis 未知）按原口径展示，不闪回退标题", () => {
    expect(navBasisLabels(undefined, "CN_OTC")).toEqual({
      curveTitle: "累计净值走势",
      returnsTitle: "区间收益率",
      note: null,
    });
  });

  it("unit 口径的净值源改成单位净值走势并注记不含分红", () => {
    const labels = navBasisLabels("unit", "HK_MUTUAL");
    expect(labels.curveTitle).toBe("单位净值走势");
    expect(labels.note).toContain("不含分红再投资");
    expect(labels.note).not.toContain("市价");
  });

  it("unit 口径的场内产品明示市价，不冒充净值", () => {
    const labels = navBasisLabels("unit", "CN_EXCHANGE");
    expect(labels.curveTitle).toBe("收盘价走势（市价）");
    expect(labels.returnsTitle).toBe("区间收益率（市价）");
    expect(labels.note).toContain("收盘价");
    expect(labels.note).toContain("不含分红再投资");
  });

  it("回退口径的注记对两个净值源市场都非空（标题改了却无解释等于换了个说法）", () => {
    expect(navBasisLabels("unit", "CN_OTC").note).toBeTruthy();
    expect(navBasisLabels("unit", "HK_MUTUAL").note).toBeTruthy();
  });
});
