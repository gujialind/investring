import { describe, it, expect } from "vitest";
import { queryKeys } from "@/lib/queryKeys";

// #344 防回归：mutation 失效键必须是列表键的真前缀，否则
// invalidateQueries 逐元素匹配失配、列表不刷新
describe("queryKeys.shareChangeEvents", () => {
  it("byPortfolio 是任意 list 键的真前缀", () => {
    const prefix = queryKeys.shareChangeEvents.byPortfolio("P1");
    expect(prefix).toEqual(["share-change-events", "P1"]);

    const listKey = queryKeys.shareChangeEvents.list("P1", { page: 1, page_size: 20 });
    expect(listKey.slice(0, prefix.length)).toEqual(prefix);
  });

  it("list(code) 少传 params 产生尾 undefined 键，并非真前缀（失效失配根因）", () => {
    const shorthand = queryKeys.shareChangeEvents.list("P1");
    expect(shorthand).toEqual(["share-change-events", "P1", undefined]);

    const listKey = queryKeys.shareChangeEvents.list("P1", { page: 1 });
    expect(listKey.slice(0, shorthand.length)).not.toEqual(shorthand);
  });

  // #424 确认预览键：与列表键同域但不互相吞掉——写成 ["share-change-events", id] 会与
  // detail 语义撞车，且被 byPortfolio 前缀失效连带刷掉（预览值即确认值，staleTime=0 自管刷新）
  it("preview 键形如 [域, id, 'preview']，与列表/前缀键均不同", () => {
    expect(queryKeys.shareChangeEvents.preview(7)).toEqual([
      "share-change-events",
      7,
      "preview",
    ]);
    expect(queryKeys.shareChangeEvents.preview(7)).not.toEqual(
      queryKeys.shareChangeEvents.preview(8),
    );
  });
});
