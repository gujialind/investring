"use client";

/**
 * 并列双行日期单元格（visual-spec §8，#355）：语义并列的两个日期上下双行，两行同 text-sm 同正文色，
 * 不做字号/色分层（区别于 NameCodeCell / ProductCell 的主次双行）。每行挂 title 标注语义。
 * 上下行槽位由调用点固定传入（上行=先发生、下行=后发生），组件内不按值排序——下行可能是 `--` 空值占位。
 * 两值相同（如场内当日确认）仍渲染两行，不折叠，保持列结构稳定。两行 nowrap 防窄列下按连字符断行。
 * estimated：pending 记录的确认日是预计值，在下行内联小字「预计」后缀标注。
 * showLabels（移动流水卡，#907）：卡片无表头、触屏无悬停，title 标签不可达，故每行改 flex justify-between
 * 渲染可见标签前缀（text-xs 灰，与 kv 格同档）+ 日期值 text-sm；桌面表格不传，仍 title-only 双行、输出不变。
 * showLabels 与 estimated 均破「同字号同色」，分别仅移动卡 / 预计态启用。
 */
export default function DatePairCell({
  topLabel,
  topValue,
  bottomLabel,
  bottomValue,
  estimated = false,
  showLabels = false,
}: {
  topLabel: string;
  topValue: string;
  bottomLabel: string;
  bottomValue: string;
  estimated?: boolean;
  showLabels?: boolean;
}) {
  const bottomTitle = estimated ? `${bottomLabel}（预计）` : bottomLabel;
  const estimatedSuffix = estimated && (
    <span className="ml-1 text-xs text-muted-foreground">预计</span>
  );

  if (showLabels) {
    return (
      <>
        <div className="flex items-baseline justify-between gap-2">
          <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{topLabel}</span>
          <span className="whitespace-nowrap text-sm" title={topLabel}>
            {topValue}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{bottomLabel}</span>
          <span className="whitespace-nowrap text-sm" title={bottomTitle}>
            {bottomValue}
            {estimatedSuffix}
          </span>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="whitespace-nowrap text-sm" title={topLabel}>
        {topValue}
      </div>
      <div className="whitespace-nowrap text-sm" title={bottomTitle}>
        {bottomValue}
        {estimatedSuffix}
      </div>
    </>
  );
}
