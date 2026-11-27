import { describe, expect, it } from "vitest";
import type { SnapshotListItem } from "@/types/snapshot";
import { getSnapshotDailyReturn } from "./portfolioSnapshot";

function snapshot(snapshot_date: string, unit_price: number): SnapshotListItem {
  return {
    snapshot_date,
    unit_price,
    total_shares: 100,
    total_value: unit_price * 100,
    in_transit_total: 0,
  };
}

describe("getSnapshotDailyReturn", () => {
  it("计算最新快照相对前序快照的百分比", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 1.05), snapshot("2026-01-01", 1.0)],
        [],
      ),
    ).toBeCloseTo(5);
  });

  it("负变化保留符号，真实零返回 0", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 0.95), snapshot("2026-01-01", 1.0)],
        [],
      ),
    ).toBeCloseTo(-5);
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 1.0), snapshot("2026-01-01", 1.0)],
        [],
      ),
    ).toBe(0);
  });

  it("无快照或仅一条快照时不伪造基准", () => {
    expect(getSnapshotDailyReturn(undefined, [])).toBeNull();
    expect(getSnapshotDailyReturn([], [])).toBeNull();
    expect(getSnapshotDailyReturn([snapshot("2026-01-02", 1.05)], [])).toBeNull();
  });

  it("前序净值非正或数据非有限时不求解", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 1.05), snapshot("2026-01-01", 0)],
        [],
      ),
    ).toBeNull();
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", -0.5), snapshot("2026-01-01", -1)],
        [],
      ),
    ).toBeNull();
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", Number.NaN), snapshot("2026-01-01", 1.0)],
        [],
      ),
    ).toBeNull();
  });

  it("比较区间存在缺失交易日时不把多日变化当日涨跌", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-05", 1.1), snapshot("2026-01-01", 1.0)],
        ["2026-01-02"],
      ),
    ).toBeNull();
  });

  it("缺失交易日不在比较区间内时保留有效变化", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 1.05), snapshot("2026-01-01", 1.0)],
        ["2025-12-31"],
      ),
    ).toBeCloseTo(5);
  });

  it("快照顺序异常时不计算", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-01", 1.0), snapshot("2026-01-02", 1.05)],
        [],
      ),
    ).toBeNull();
  });

  it("连续性状态尚未读取时保守不显示", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 1.05), snapshot("2026-01-01", 1.0)],
        undefined,
      ),
    ).toBeNull();
  });

  it("连续性状态为 null 时不抛异常", () => {
    expect(
      getSnapshotDailyReturn(
        [snapshot("2026-01-02", 1.05), snapshot("2026-01-01", 1.0)],
        null,
      ),
    ).toBeNull();
  });
});
