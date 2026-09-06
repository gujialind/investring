"use client";

/**
 * 主次双行单元格（visual-spec §8）：name 主行 + code 次要小字；映射缺失时回退只显示 code。
 * #124 申赎页投资人/平台列首创，#126 提取共用（申赎/调仓两页平台列同模式）。
 */
export default function NameCodeCell({ code, nameMap }: { code: string; nameMap: Map<string, string> }) {
  const name = nameMap.get(code);
  if (!name) return <>{code}</>;
  return (
    <>
      {/* 名称长度有界，nowrap 防表格宽度紧张时 CJK 逐字竖排（#389） */}
      <div className="text-sm whitespace-nowrap">{name}</div>
      <div className="text-xs text-muted-foreground">{code}</div>
    </>
  );
}
