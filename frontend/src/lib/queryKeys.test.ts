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

// #484：全域工厂。key 是 queryClient 路由缓存与失效的唯一坐标系——域段改名会让
// invalidateQueries 的值相等匹配静默失配（#344 的同类事故），root 不是真前缀则
// 「失效整域」刷不掉子键。两处都用字面量断言钉死，让改动必须先过测试。
describe("queryKeys 全域工厂", () => {
  it("各域 root 与域段字面量一致（root 缺席域单独钉其唯一键）", () => {
    expect(queryKeys.investors.root).toEqual(["investors"]);
    expect(queryKeys.portfolios.root).toEqual(["portfolios"]);
    expect(queryKeys.positions.root).toEqual(["positions"]);
    expect(queryKeys.trades.root).toEqual(["trades"]);
    expect(queryKeys.subscriptions.root).toEqual(["subscriptions"]);
    expect(queryKeys.products.root).toEqual(["products"]);
    expect(queryKeys.platforms.root).toEqual(["platforms"]);
    expect(queryKeys.snapshots.root).toEqual(["snapshots"]);
    expect(queryKeys.shareChangeEvents.root).toEqual(["share-change-events"]);
    expect(queryKeys.cashTransfers.root).toEqual(["cash-transfers"]);
    expect(queryKeys.tasks.root).toEqual(["tasks"]);
    expect(queryKeys.tradingCalendar.root).toEqual(["trading-calendar"]);
    // dataSources 域无 root，仅单键查询
    expect(queryKeys.dataSources.config()).toEqual(["data-source-config"]);
  });

  it("root 是域内所有键的真前缀（失效整域命中全部子键）", () => {
    const cases: { root: readonly unknown[]; keys: readonly (readonly unknown[])[] }[] = [
      {
        root: queryKeys.investors.root,
        keys: [
          queryKeys.investors.list(),
          queryKeys.investors.list({ page: 1 }),
          queryKeys.investors.detail("I1"),
        ],
      },
      {
        root: queryKeys.portfolios.root,
        keys: [queryKeys.portfolios.list(), queryKeys.portfolios.detail("P1")],
      },
      {
        root: queryKeys.positions.root,
        keys: [
          queryKeys.positions.byPortfolio("P1"),
          queryKeys.positions.list("P1"),
          queryKeys.positions.list("P1", { page: 1 }),
        ],
      },
      {
        root: queryKeys.trades.root,
        keys: [
          queryKeys.trades.list(),
          queryKeys.trades.detail(1),
          queryKeys.trades.preview(1),
          queryKeys.trades.previewWith(1),
          queryKeys.trades.previewWith(1, { cash_confirm_date: "2026-09-18" }),
        ],
      },
      {
        root: queryKeys.subscriptions.root,
        keys: [
          queryKeys.subscriptions.list(),
          queryKeys.subscriptions.detail(1),
          queryKeys.subscriptions.preview(1),
        ],
      },
      {
        root: queryKeys.products.root,
        keys: [
          queryKeys.products.list(),
          queryKeys.products.detail("000001", "CN_OTC"),
          queryKeys.products.prices("000001", "CN_OTC"),
          queryKeys.products.prices(),
        ],
      },
      {
        root: queryKeys.platforms.root,
        keys: [queryKeys.platforms.list(), queryKeys.platforms.detail("X1")],
      },
      { root: queryKeys.snapshots.root, keys: [queryKeys.snapshots.status("P1")] },
      {
        root: queryKeys.shareChangeEvents.root,
        keys: [
          queryKeys.shareChangeEvents.byPortfolio("P1"),
          queryKeys.shareChangeEvents.list("P1", { page: 1 }),
          queryKeys.shareChangeEvents.preview(1),
        ],
      },
      {
        root: queryKeys.cashTransfers.root,
        keys: [queryKeys.cashTransfers.list("P1"), queryKeys.cashTransfers.list("P1", { page: 1 })],
      },
      {
        root: queryKeys.tasks.root,
        keys: [queryKeys.tasks.list(), queryKeys.tasks.executions(), queryKeys.tasks.executions({ page: 1 })],
      },
      { root: queryKeys.tradingCalendar.root, keys: [queryKeys.tradingCalendar.byYear(2026)] },
    ];

    for (const { root, keys } of cases) {
      for (const key of keys) {
        expect(key.slice(0, root.length)).toEqual(root);
        // 真前缀：子键必须比 root 长，否则「失效整域」与子键互相误伤
        expect(key.length).toBeGreaterThan(root.length);
      }
    }
  });
});

// #493 §3.4.3：预览键必须把**有效业务选项**纳入——到账日/到账平台/确认日/价格任一变化
// 都要换 key 触发重新预览，否则「预览值即确认值」的约定会被过期预览破坏。
describe("queryKeys.trades.previewWith（#493 有效选项分键）", () => {
  it("同一交易、不同有效现金选项 → 不同 key", () => {
    const base = queryKeys.trades.previewWith(7, { confirm_date: "2026-09-18" });
    const otherDate = queryKeys.trades.previewWith(7, {
      confirm_date: "2026-09-18",
      cash_confirm_date: "2026-09-21",
    });
    const otherPlatform = queryKeys.trades.previewWith(7, {
      confirm_date: "2026-09-18",
      cash_platform_code: "TTJJ",
    });
    expect(otherDate).not.toEqual(base);
    expect(otherPlatform).not.toEqual(base);
    expect(otherDate).not.toEqual(otherPlatform);
  });

  it("缺省值（undefined）与显式值分属不同 key，缺省统一落 null 槽位", () => {
    expect(queryKeys.trades.previewWith(7)).toEqual([
      "trades",
      7,
      "preview",
      null,
      null,
      null,
      null,
    ]);
    expect(queryKeys.trades.previewWith(7)).toEqual(queryKeys.trades.previewWith(7, {}));
  });

  it("previewWith 键前缀覆盖 preview(id) 家族（无选项时前 3 段一致）", () => {
    expect(queryKeys.trades.previewWith(7).slice(0, 3)).toEqual(queryKeys.trades.preview(7));
  });
});
