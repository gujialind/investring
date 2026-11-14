import { describe, it, expect } from "vitest";
import { DATA_SOURCE_OPTIONS, availableDataSources } from "./dataSource";

describe("availableDataSources", () => {
  it("HK_MUTUAL 只返回 akshare（tushare 不支持港互认）", () => {
    expect(availableDataSources("HK_MUTUAL")).toEqual(["akshare"]);
  });

  it("CN_OTC / CN_EXCHANGE 返回两源", () => {
    expect(availableDataSources("CN_OTC")).toEqual(["tushare", "akshare"]);
    expect(availableDataSources("CN_EXCHANGE")).toEqual(["tushare", "akshare"]);
  });

  it("空/未知市场返回两源（现金类是否显示控件由调用方判定）", () => {
    expect(availableDataSources(undefined)).toEqual([...DATA_SOURCE_OPTIONS]);
    expect(availableDataSources("")).toEqual(["tushare", "akshare"]);
  });
});
