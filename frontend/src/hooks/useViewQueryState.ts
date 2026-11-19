"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { parseHoldingsView, type HoldingsView } from "@/types/holding";
import {
  parseSubDimOverrides,
  registeredDimsFor,
  serializeSubDimOverrides,
  toggleSubDimOverride,
  type DimensionRules,
  type SubDimension,
  type SubDimOverrides,
} from "@/lib/dimensions";

/**
 * 组合详情页视图状态 URL 单点（#595 D-7 ?view= ＋ #915 B2 ?sub=，双端共用）。
 *
 * 竞态口径（ocr r2/r3）：router.replace 的 URL 在 navigation commit 后才回流到
 * useSearchParams，渲染期快照做合并基会让连点两次丢首写（?sub= 是多键累积 map，
 * 丢更新用户可见）。故用 pending state + **函数式 setState 累积**（updater 按排队
 * 顺序拿到前值，连点不丢），effect 驱动 router.replace；commit 回流后清 pending。
 * 渲染期读 effectiveQs（pending 优先且同 pathname）——pressed 态即时反馈。
 *
 * pending 快照四元组 { qs, base, path, issued }：
 * - issued = 本链交给 router.replace 的全部 qs（ocr r3 high）：多个在途写时，
 *   较早的自身 commit（committed ∈ issued 且 ≠ qs）只推进 base、不清 pending，
 *   否则第二次点击会被误判外部导航而丢失；
 * - path（ocr r3 medium）：App Router 同组件跨组合导航（/portfolio/A→B）复用
 *   实例、pending 会存活；pathname 变了即外部变更，清 pending 防把 A 的在途写
 *   回放到 B 的 URL；
 * - 外部导航（回退/深链）：committed ∉ issued 且 ≠ base → 清 pending 跟随 URL。
 *   已知窄歧义：pending 期间回退到恰好等于 issued 中某串时会被当作自身中间
 *   commit（窗口 < 一次 commit，接受）。
 *
 * 不写回 display_config、不引入浏览器存储：?sub= 纯视图状态（SPEC §9.4 C-F 读法）。
 */
export function useViewQueryState(dimensionRules?: DimensionRules | null) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const committedQs = searchParams.toString();
  const [pending, setPending] = useState<{
    qs: string;
    base: string;
    path: string;
    issued: string[];
  } | null>(null);
  const effectiveQs = pending && pending.path === pathname ? pending.qs : committedQs;

  const writeQs = (mutate: (sp: URLSearchParams) => void) => {
    setPending((prev) => {
      const chain = prev && prev.path === pathname ? prev : null;
      const sp = new URLSearchParams(chain?.qs ?? committedQs);
      mutate(sp);
      const qs = sp.toString();
      return {
        qs,
        base: chain?.base ?? committedQs,
        path: pathname,
        issued: [...(chain?.issued ?? []), qs],
      };
    });
  };

  useEffect(() => {
    if (!pending) return;
    if (pending.path !== pathname) {
      setPending(null);
      return;
    }
    if (pending.qs === committedQs) {
      setPending(null);
      return;
    }
    if (pending.issued.includes(committedQs)) {
      // 自身中间 commit：推进 base 保留 pending（guard 防更新循环）
      setPending((prev) =>
        prev && prev.base !== committedQs ? { ...prev, base: committedQs } : prev
      );
      return;
    }
    if (committedQs !== pending.base) {
      setPending(null);
      return;
    }
    router.replace(pending.qs ? `${pathname}?${pending.qs}` : pathname, { scroll: false });
  }, [pending, committedQs, pathname, router]);

  const sp = new URLSearchParams(effectiveQs);
  const view = parseHoldingsView(sp.get("view"));
  const subDimOverrides = parseSubDimOverrides(sp.get("sub"));

  const onViewChange = (next: HoldingsView) =>
    writeQs((q) => {
      if (next === "product") q.delete("view");
      else q.set("view", next);
    });

  /** 切换意图回调（dim=null = 「默认」项清除该大类覆盖）；登记维判据走 lib 单点
   * （rules 缺失 = null 不校验，ocr r3 low：不得传空数组冒充「全禁止」） */
  const onSubDimToggle = (classCode: string, dim: SubDimension | null) =>
    writeQs((q) => {
      const base = parseSubDimOverrides(q.get("sub"));
      let next: SubDimOverrides;
      if (dim === null) {
        next = { ...base };
        delete next[classCode];
      } else {
        next = toggleSubDimOverride(base, classCode, dim, registeredDimsFor(classCode, dimensionRules));
      }
      const serialized = serializeSubDimOverrides(next);
      if (serialized) q.set("sub", serialized);
      else q.delete("sub");
    });

  return { view, onViewChange, subDimOverrides, onSubDimToggle };
}