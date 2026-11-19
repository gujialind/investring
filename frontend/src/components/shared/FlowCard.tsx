"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * 流水卡解剖共享单点（#907，visual-spec §8「流水卡解剖」）。
 * 调仓/申赎/份额变动事件/快照四页移动分支共用同一副卡解剖，消除横滚与 CJK 竖排：
 * 头行（名称 + code·市场·平台次行 / 方向·类型 chip neutral / 状态 badge）→ kv 数值网格
 * （mono + format 函数 + number-cell 族）→ 日期行（双值均可见、pending 带「预计」）→
 * 操作行（RowActionButton 图标档、仅主行）。结对子行 → 卡内子块（muted 底 + xs + 标签
 * nowrap、无操作入口）。容器形态承 #897 管理页移动卡片（rounded-lg border p-3、无阴影）。
 * 字段集合双端一致（§6）：卡呈现的字段与桌面列逐一对应，不为移动端单独裁剪信息结构。
 * 子块文案/状态判据消费 lib/tradePairs.ts 单点，不在此重新推导。
 */

/** 卡容器：#897 同形（rounded-lg border p-3、无阴影），space-y-2分隔解剖各段。 */
export function FlowCard({
  testId,
  className,
  children,
}: {
  testId?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div data-testid={testId} className={cn("rounded-lg border p-3 space-y-2", className)}>
      {children}
    </div>
  );
}

/**
 * 头行：左 = 名称主行（text-sm font-medium，长名允许换行不撑破卡）+ code·市场·平台次行
 * （xs muted nowrap）；右 = 方向/类型 chip + 状态 badge（aside 槽，shrink-0 不被名称挤压）。
 */
export function FlowCardHeader({
  title,
  meta,
  aside,
}: {
  title: ReactNode;
  meta?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <div className="text-sm font-medium">{title}</div>
        {meta != null && (
          <div className="whitespace-nowrap text-xs text-muted-foreground">{meta}</div>
        )}
      </div>
      {aside != null && <div className="flex shrink-0 items-center gap-1.5">{aside}</div>}
    </div>
  );
}

/**
 * kv 数值网格：两列 label/value，值走 number-cell 族（右对齐 mono tabular-nums nowrap），
 * label xs muted、nowrap+shrink-0——长值（如 100,000.00 份）会把可压缩的 label 挤成 CJK 竖排，
 * 故 label 先占定宽再让值右对齐（#907 目检实测）。数值由调用点经 format 函数格式化后传入
 * （pending 空值 fallback `--`，#424）。testId 挂在值 span（非整格）——E2E 对值文本做精确断言，
 * 含 label 会污染 textContent。
 */
export function FlowCardKVGrid({
  items,
}: {
  items: { label: string; value: ReactNode; testId?: string }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1">
      {items.map((item) => (
        <div key={item.label} className="flex items-baseline justify-between gap-2">
          <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
            {item.label}
          </span>
          <span className="number-cell text-sm" data-testid={item.testId}>
            {item.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 操作行：RowActionButton 图标档，仅主行渲染（子块无操作入口）。右对齐成组。 */
export function FlowCardActions({ children }: { children: ReactNode }) {
  return <div className="flex items-center justify-end gap-1">{children}</div>;
}

/**
 * 结对子块：muted 底 + xs + 标签 nowrap（#733），无操作入口、不响应 hover。
 * label 槽承现金腿/派生对手腿标签（文案来自 tradePairs）；children 为右侧金额/份额@净值·费/
 * 生效日槽位，颜色由调用点按 §8 语义控制（真现金金额 text-foreground 手工 ±、派生金额无 ±、
 * 份额@净值与生效日 muted）。
 */
export function FlowCardSubBlock({
  testId,
  label,
  children,
}: {
  testId?: string;
  label: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div
      data-testid={testId}
      className="flex items-center justify-between gap-2 rounded bg-muted/50 px-2 py-1"
    >
      <span className="whitespace-nowrap text-xs text-muted-foreground">{label}</span>
      {children != null && <span className="flex items-center gap-2 text-xs">{children}</span>}
    </div>
  );
}