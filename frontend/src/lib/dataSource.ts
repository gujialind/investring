/** 数据源选项与按市场过滤（issue #880，SPEC v1.1 §2.1.6）。
 * tushare 不支持港互认（HK_MUTUAL 只 akshare）；与后端 product_service.validate_data_source 同口径。
 * 产品管理列表筛选与产品表单下拉共用本常量，避免两处硬编码漂移。 */
export const DATA_SOURCE_OPTIONS = ["tushare", "akshare"] as const;

/** 按市场返回可选数据源：港互认只 akshare；其余市场两源皆可。
 * 现金/虚拟产品（market 空）由调用方判定是否显示控件，本函数不做该收窄。 */
export function availableDataSources(market: string | undefined): string[] {
  if (market === "HK_MUTUAL") return ["akshare"];
  return [...DATA_SOURCE_OPTIONS];
}

/** 数据源切换后果提示（L2 Nit：日涨跌口径差与分红后果仅适用场外 CN_OTC；
 * 场内两源均用上游收盘价涨跌幅、港互认只 akshare，不存在「tushare 累计 vs akshare 单位」口径差）。 */
export function dataSourceSwitchHint(market: string | undefined): string {
  const base =
    "切换后新净值来自新数据源；历史净值默认保留，最近约 10 个交易日将在下次同步时被新源覆盖。已生成快照不会自动重算，如需账实一致请手动重算。";
  if (market !== "CN_OTC") return base;
  return (
    base +
    "日涨跌列口径随源变化（tushare=累计净值总收益口径，akshare=单位净值口径含除息跳水），该列数字会系统性变化，非数据错误。切到 akshare 的场外产品暂不自动检测分红（akshare 分红检测落地前），请按公告手工录入。"
  );
}
