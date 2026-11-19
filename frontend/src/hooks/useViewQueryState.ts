"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { parseHoldingsView, type HoldingsView } from "@/types/holding";
import {
  parseSubDimOverrides,
  serializeSubDimOverrides,
  toggleSubDimOverride,
  type DimensionRules,
  type SubDimension,
  type SubDimOverrides,
} from "@/lib/dimensions";

/**
 * 组合详情页视图状态 URL 单点（#595 D-7 ?view= ＋ #915 B2 ?sub=，双端共用）。
 *
 * 竞态口径（ocr r2）：router.replace 的 URL 在 navigation commit 后才回流到
 * useSearchParams，渲染期快照做合并基会让连点两次丢首写（?sub= 是多键累积 map，
 * 丢更新用户可见）。故用 pending state + **函数式 setState 累积**（updater 按排队
 * 顺序拿到前值，连点不丢），effect 驱动 router.replace；commit 回流后清 pending。
 * 渲染期读 effectiveQs（pending 优先）——pressed 态即时反馈，不等 commit。
 *
 * 外部导航（回退/深链）对齐：committed ≠ pending.qs 且 ≠ pending.base（写入前的
 * committed）时判为外部变更，清 pending 跟随 URL。自身 commit 命中第一分支
 * （committed === pending.qs），不会误判。
 *
 * 不写回 display_config、不引入浏览器存储：?sub= 纯视图状态（SPEC §9.4 C-F 读法）。
 */
export function useViewQueryState(dimensionRules?: DimensionRules | null) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const committedQs = searchParams.toString();
  const [pending, setPending] = useState<{ qs: string; base: string } | null>(null);
  const effectiveQs = pending?.qs ?? committedQs;

  const writeQs = (mutate: (sp: URLSearchParams) => void) => {
    setPending((prev) => {
      const base = prev?.base ?? committedQs;
      const sp = new URLSearchParams(prev?.qs ?? committedQs);
      mutate(sp);
      return { qs: sp.toString(), base };
    });
  };

  useEffect(() => {
    if (!pending) return;
    if (pending.qs === committedQs) {
      setPending(null);
      return;
    }
    if (committedQs !== pending.base) {
      setPending(null);
      return;
    }
    router.replace(pending.qs ? `${pathname}?${pending.qs}` : pathname, { scroll: false });
  }, [pending, committedQs, router, pathname]);

  const sp = new URLSearchParams(effectiveQs);
  const view = parseHoldingsView(sp.get("view"));
  const subDimOverrides = parseSubDimOverrides(sp.get("sub"));

  const onViewChange = (next: HoldingsView) =>
    writeQs((q) => {
      if (next === "product") q.delete("view");
      else q.set("view", next);
    });

  /** 切换意图回调（dim=null = 「默认」项清除该大类覆盖）；登记维判据与 lib toggle
   * 同口径（失效覆盖首击重建子集，ocr r1 死胡同） */
  const onSubDimToggle = (classCode: string, dim: SubDimension | null) =>
    writeQs((q) => {
      const base = parseSubDimOverrides(q.get("sub"));
      let next: SubDimOverrides;
      if (dim === null) {
        next = { ...base };
        delete next[classCode];
      } else {
        const registered = Object.keys(dimensionRules?.[classCode] ?? {});
        next = toggleSubDimOverride(base, classCode, dim, registered);
      }
      const serialized = serializeSubDimOverrides(next);
      if (serialized) q.set("sub", serialized);
      else q.delete("sub");
    });

  return { view, onViewChange, subDimOverrides, onSubDimToggle };
}