import type { NavBasis } from "@/lib/api/product";

/**
 * 净值口径 → 卡片标题与注记（#703）。
 *
 * 后端在产品整段历史都没有累计净值时回退到单位净值口径（`nav_basis="unit"`），数字仍
 * 从 `accumulated_nav` 字段返回。回退口径与原口径**不是同一种收益**：不含分红再投资，
 * 场内产品的 `unit_price` 还是二级市场收盘价（含折溢价）而非净值。标题照旧写「累计
 * 净值走势」就是把回退口径的数字标成累计净值，所以文案必须由 basis 驱动。
 *
 * 两个详情页（产品页与平台-产品页）各有一份同形卡片，故口径文案单点在此，避免漂移。
 */

export interface NavBasisLabels {
  /** 曲线卡标题 */
  curveTitle: string;
  /** 区间收益率卡标题 */
  returnsTitle: string;
  /** 口径注记；`accumulated`（含首屏在途的 undefined）为 null——原口径无需解释 */
  note: string | null;
}

const ACCUMULATED_LABELS: NavBasisLabels = {
  curveTitle: "累计净值走势",
  returnsTitle: "区间收益率",
  note: null,
};

export function navBasisLabels(
  basis: NavBasis | undefined,
  market: string | undefined
): NavBasisLabels {
  if (basis !== "unit") {
    return ACCUMULATED_LABELS;
  }
  if (market === "CN_EXCHANGE") {
    return {
      curveTitle: "收盘价走势（市价）",
      returnsTitle: "区间收益率（市价）",
      note: "场内行情源不提供净值：按二级市场收盘价计算，含折溢价、不含分红再投资",
    };
  }
  return {
    curveTitle: "单位净值走势",
    returnsTitle: "区间收益率",
    note: "该产品上游无累计净值：按单位净值计算，不含分红再投资",
  };
}
