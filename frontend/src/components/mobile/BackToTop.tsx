"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BACK_TO_TOP_VISIBILITY_MULTIPLIER } from "./backToTopConfig";

interface BackToTopProps {
  /** 按钮因滚动隐藏时，把焦点交还到仍可达的页头出口。 */
  returnFocusSelector: string;
}

/** 移动组合详情长页回顶；非模态控制层，层级始终低于 Sheet/Dialog（#937）。 */
export default function BackToTop({ returnFocusSelector }: BackToTopProps) {
  const [visible, setVisible] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRequestedRef = useRef(false);

  useEffect(() => {
    const updateVisibility = () => {
      const shouldShow = window.scrollY >= BACK_TO_TOP_VISIBILITY_MULTIPLIER * window.innerHeight;
      if (!shouldShow && document.activeElement === buttonRef.current) {
        returnFocusRequestedRef.current = true;
      }
      setVisible(shouldShow);
    };

    updateVisibility();
    window.addEventListener("scroll", updateVisibility, { passive: true });
    window.addEventListener("resize", updateVisibility, { passive: true });
    return () => {
      window.removeEventListener("scroll", updateVisibility);
      window.removeEventListener("resize", updateVisibility);
    };
  }, [returnFocusSelector]);

  useEffect(() => {
    if (visible || !returnFocusRequestedRef.current) return;
    // 等按钮卸载与 PageHeader 展开/折叠同批提交后再找出口，避免焦点落在被替换的旧节点上。
    document.querySelector<HTMLElement>(returnFocusSelector)?.focus({ preventScroll: true });
    returnFocusRequestedRef.current = false;
  }, [returnFocusSelector, visible]);

  if (!visible) return null;

  return (
    <Button
      ref={buttonRef}
      type="button"
      variant="outline"
      size="icon"
      className="fixed right-[calc(1rem_+_env(safe-area-inset-right,0px))] bottom-[calc(1rem_+_env(safe-area-inset-bottom,0px))] z-40 h-11 w-11 rounded-full"
      aria-label="回到顶部"
      title="回到顶部"
      data-testid="back-to-top"
      onClick={() => window.scrollTo(0, 0)}
    >
      <ArrowUp className="h-5 w-5" aria-hidden="true" />
    </Button>
  );
}
