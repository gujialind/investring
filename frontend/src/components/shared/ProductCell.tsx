"use client";

import { formatMarketName } from "@/lib/utils";

/**
 * 产品主次双行单元格（visual-spec §8）：名称主行 + `代码 · 市场名` 次行；名称缺失回退单行只显示代码。
 * 次行后缀对齐 #259 产品选择器语言；market 为空（CASH / 在途虚拟产品）不拼后缀，避免 `· --` 残留。
 * 次行 nowrap——`代码 · 市场名` 长度有界，防窄列下在中点处折行重演「市场」列竖排（#355）；
 * 主行不 nowrap，长基金名需允许换行以免撑破列宽。
 * #355 调仓页与份额变动页产品列提取共用，消除两页样式漂移。
 */
export default function ProductCell({
  code,
  name,
  market,
}: {
  code: string;
  name?: string;
  market?: string;
}) {
  const suffix = market ? ` · ${formatMarketName(market)}` : "";
  if (!name) {
    return (
      <div className="whitespace-nowrap text-sm">
        {code}
        {suffix}
      </div>
    );
  }
  return (
    <>
      <div className="text-sm font-medium">{name}</div>
      <div className="whitespace-nowrap text-xs text-muted-foreground">
        {code}
        {suffix}
      </div>
    </>
  );
}
