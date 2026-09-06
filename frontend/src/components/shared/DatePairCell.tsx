"use client";

/**
 * 并列双行日期单元格（visual-spec §8，#355）：语义并列的两个日期上下双行，两行同 text-sm 同正文色，
 * 不做字号/色分层（区别于 NameCodeCell / ProductCell 的主次双行）。每行挂 title 标注语义。
 * 上下行槽位由调用点固定传入（上行=先发生、下行=后发生），组件内不按值排序——下行可能是 `--` 空值占位。
 * 两值相同（如场内当日确认）仍渲染两行，不折叠，保持列结构稳定。两行 nowrap 防窄列下按连字符断行。
 * estimated：pending 记录的确认日是预计值，在下行内联小字后缀标注——本模式唯一破「同字号同色」处。
 */
export default function DatePairCell({
  topLabel,
  topValue,
  bottomLabel,
  bottomValue,
  estimated = false,
}: {
  topLabel: string;
  topValue: string;
  bottomLabel: string;
  bottomValue: string;
  estimated?: boolean;
}) {
  return (
    <>
      <div className="whitespace-nowrap text-sm" title={topLabel}>
        {topValue}
      </div>
      <div
        className="whitespace-nowrap text-sm"
        title={estimated ? `${bottomLabel}（预计）` : bottomLabel}
      >
        {bottomValue}
        {estimated && <span className="ml-1 text-xs text-muted-foreground">预计</span>}
      </div>
    </>
  );
}
