import type { SnapshotListItem } from "@/types/snapshot";

export const SNAPSHOT_DAILY_RETURN_WINDOW = 2;

/** items 为倒序 limit=SNAPSHOT_DAILY_RETURN_WINDOW 响应；missingDates 来自 status，连续性未知或区间有缺口时不显示。 */
export function getSnapshotDailyReturn(
  items: SnapshotListItem[] | undefined,
  missingDates: string[] | null | undefined,
): number | null {
  if (!items || !missingDates) return null;
  const [latest, previous] = items;
  if (!latest || !previous || latest.snapshot_date <= previous.snapshot_date) return null;
  if (!Number.isFinite(latest.unit_price) || !Number.isFinite(previous.unit_price)) return null;
  if (previous.unit_price <= 0) return null;
  if (missingDates.some((day) => day > previous.snapshot_date && day < latest.snapshot_date)) {
    return null;
  }
  const change = (latest.unit_price / previous.unit_price - 1) * 100;
  return Number.isFinite(change) ? change : null;
}
