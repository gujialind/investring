/**
 * 调仓买入/卖出金额联动纯函数（issue #193：买入金额双字段联动）。
 * 买入：实际支付（含费）↔ 净投入（扣费后）双向联动；卖出：金额纯派生量（#190）的展示侧推导。
 * 量化口径对齐后端 `app/utils/quantize.py::quantize_amount`（2 位小数、ROUND_HALF_UP、负数对称）。
 */

/**
 * 金额量化到 2 位小数（ROUND_HALF_UP，负数按绝对值对称、远离零进位）。
 * 与后端同用「字符串位移」口径（后端 Decimal(str(x))），
 * 避免 JS toFixed/Math.round 在 1.005 类二进制浮点边界值上错判。
 * 非法输入（空串/非数值）返回 null；字符串位移无法安全量化的输入同样返回 null（#504）：
 * ① `|value| ≥ 1e19`：取整值 ≥ 1e21，`String(rounded)` 转指数记法（数字与十进制串均命中）；
 * ② 数字入参 `0 < |value| < 1e-6`：`String(value)` 即指数记法（同量级十进制串如 `"0.0000001"`
 * 不命中，按常规路径返回 0）；③ 入参本身为指数记法字符串（`"1e5"`/`"1e-7"`）。
 * 注：这些量级下后端 `Decimal(str(x))` 仍能算出结果（如 `1e-7 → 0.00`），前端按「不可量化
 * 即 null（调用方清空/不渲染）」收口；不做数字归一化，以免动摇上面 1.005 的字符串位移口径。
 */
export function quantizeAmount2(value: number | string): number | null {
  const s = typeof value === "string" ? value.trim() : String(value);
  if (s === "" || !Number.isFinite(Number(s))) return null;
  const shifted = Number(`${s}e2`);
  const rounded = Math.sign(shifted) * Math.round(Math.abs(shifted));
  const result = Number(`${rounded}e-2`);
  // 只守卫入参不够：字符串位移在指数记法边界会产出 NaN，须落回 null（#504）
  return Number.isFinite(result) ? result : null;
}

/** 2 位小数字符串（供派生字段回填，组件内禁 toFixed 走此处，视觉规范 §3） */
export function formatAmount2(value: number): string {
  const q = quantizeAmount2(value);
  return q === null ? "" : q.toFixed(2);
}

/** 手续费解析：空串按 0（表单缺省语义），其余按金额量化，非法返回 null */
function parseFee(fee: number | string): number | null {
  return fee === "" ? 0 : quantizeAmount2(fee);
}

/** 净投入 = 实际支付 − 手续费（量化后）；实付缺失/非法返回 null */
export function netFromActual(actual: number | string, fee: number | string): number | null {
  const a = quantizeAmount2(actual);
  const f = parseFee(fee);
  if (a === null || f === null) return null;
  return quantizeAmount2(a - f);
}

/** 实际支付 = 净投入 + 手续费（量化后）；净额缺失/非法返回 null */
export function actualFromNet(net: number | string, fee: number | string): number | null {
  const n = quantizeAmount2(net);
  const f = parseFee(fee);
  if (n === null || f === null) return null;
  return quantizeAmount2(n + f);
}

/**
 * 卖出派生金额（镜像后端 `_derive_sell_amounts` 有价分支，#190）：
 * 毛额 = quantize(份额 × 价格)、到手 = 毛额 − 手续费。
 * 份额或价格缺失/非法（场外未传价）返回 null → 展示侧不渲染。
 */
export function sellDerivedAmounts(
  shares: number | string,
  price: number | string,
  fee: number | string
): { gross: number; actualReceived: number } | null {
  const sh = quantizeAmount2(shares);
  const pStr = typeof price === "string" ? price.trim() : String(price);
  const p = pStr === "" ? NaN : Number(pStr);
  const f = parseFee(fee);
  if (sh === null || !Number.isFinite(p) || f === null) return null;
  const gross = quantizeAmount2(sh * p);
  if (gross === null) return null;
  // 守卫保留（#504 判定）：gross 与 f 异号时差值可越过 1e19 重新落入不可量化区
  //（例 sellDerivedAmounts(9e18, 1, -9e18)）；全非负输入下不可达，但纯函数不做此假设。
  const actualReceived = quantizeAmount2(gross - f);
  if (actualReceived === null) return null;
  return { gross, actualReceived };
}

function formatDerived(value: number | null): string {
  return value === null ? "" : formatAmount2(value);
}

/**
 * 买入双字段联动状态推进（#193）：返回联动后的 { actual, net } 字符串。
 * changed 为手改字段（保留用户原始输入）；changed="fee" 时按 anchor 字段重算另一字段，
 * 锚点字段为空/非法则清空另一字段。
 */
export function applyBuyAmountLinkage(
  changed: "actual" | "net" | "fee",
  anchor: "actual" | "net",
  fields: { actual: string; net: string; fee: string }
): { actual: string; net: string } {
  const { actual, net, fee } = fields;
  if (changed === "actual" || (changed === "fee" && anchor === "actual")) {
    return { actual, net: formatDerived(netFromActual(actual, fee)) };
  }
  return { actual: formatDerived(actualFromNet(net, fee)), net };
}
