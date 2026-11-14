import { describe, it, expect } from "vitest";
import { availableDataSources, dataSourceSwitchHint } from "./dataSource";

describe("availableDataSources", () => {
  it("HK_MUTUAL 只返回 akshare（tushare 不支持港互认）", () => {
    expect(availableDataSources("HK_MUTUAL")).toEqual(["akshare"]);
  });

  it("CN_OTC / CN_EXCHANGE 返回两源", () => {
    expect(availableDataSources("CN_OTC")).toEqual(["tushare", "akshare"]);
    expect(availableDataSources("CN_EXCHANGE")).toEqual(["tushare", "akshare"]);
  });

  it("空/未知市场返回两源（现金类是否显示控件由调用方判定）", () => {
    // 期望值用字面量，不用被测模块自身常量自证（L2 F2）
    expect(availableDataSources(undefined)).toEqual(["tushare", "akshare"]);
    expect(availableDataSources("")).toEqual(["tushare", "akshare"]);
  });
});

describe("dataSourceSwitchHint", () => {
  it("场外 CN_OTC 含日涨跌口径差与分红后果提示", () => {
    const h = dataSourceSwitchHint("CN_OTC");
    expect(h).toContain("日涨跌列口径随源变化");
    expect(h).toContain("暂不自动检测分红");
    expect(h).toContain("切换后新净值来自新数据源");
  });

  it("场内/港互认/空市场不含日涨跌口径差提示（Nit 按市场限定）", () => {
    for (const m of ["CN_EXCHANGE", "HK_MUTUAL", undefined, ""]) {
      const h = dataSourceSwitchHint(m);
      expect(h).not.toContain("日涨跌列口径随源变化");
      expect(h).not.toContain("暂不自动检测分红");
      expect(h).toContain("切换后新净值来自新数据源");
    }
  });
});
