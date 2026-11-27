"use client";

import { useEffect, useId, useRef, useState, type PointerEvent } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

interface InfoTipProps {
  label: string;
  content: string;
  triggerClassName?: string;
}

export default function InfoTip({ label, content, triggerClassName }: InfoTipProps) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const suppressFocusOpen = useRef(false);
  const focusPinned = useRef(false);
  const focusWithinContent = useRef(false);
  const lastPointerType = useRef<string | null>(null);

  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };

  const closeSoon = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      focusPinned.current = false;
      focusWithinContent.current = false;
      setOpen(false);
    }, 120);
  };

  const canHover = () => window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const hoverCloseSuppressed = () => focusPinned.current || focusWithinContent.current;
  const lastPressWasTouch = () =>
    lastPointerType.current === "touch" || lastPointerType.current === "pen";

  const handleHoverEnd = (event: PointerEvent) => {
    if (event.pointerType === "mouse" && canHover() && !hoverCloseSuppressed()) {
      closeSoon();
    }
  };

  useEffect(() => {
    const releaseFocusPin = () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
      closeTimer.current = null;
      focusPinned.current = false;
      focusWithinContent.current = false;
      setOpen(false);
    };
    window.addEventListener("blur", releaseFocusPin);
    return () => {
      window.removeEventListener("blur", releaseFocusPin);
      if (closeTimer.current) clearTimeout(closeTimer.current);
    };
  }, []);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        cancelClose();
        if (!next) focusPinned.current = false;
        setOpen(next);
      }}
    >
      <span className={cn("inline-flex h-11 w-11 shrink-0 items-center justify-center", triggerClassName)}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={label}
            aria-expanded={open}
            aria-controls={open ? contentId : undefined}
            aria-describedby={open ? contentId : undefined}
            className="inline-flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            onPointerDown={(event) => {
              lastPointerType.current = event.pointerType;
              suppressFocusOpen.current = false;
              focusPinned.current = false;
            }}
            onKeyDown={(event) => {
              if (event.key === "Tab") suppressFocusOpen.current = false;
            }}
            onPointerEnter={(event) => {
              if (event.pointerType === "mouse" && canHover()) {
                cancelClose();
                setOpen(true);
              }
            }}
            onPointerLeave={handleHoverEnd}
            onFocus={(event) => {
              if (suppressFocusOpen.current) {
                suppressFocusOpen.current = false;
                return;
              }
              if (event.currentTarget.matches(":focus-visible")) {
                focusPinned.current = true;
                cancelClose();
                setOpen(true);
              }
            }}
            onBlur={(event) => {
              const next = event.relatedTarget as Node | null;
              if (next && contentRef.current?.contains(next)) {
                focusPinned.current = false;
                return;
              }
              if (!next && open) return;
              focusPinned.current = false;
              closeSoon();
            }}
            onClick={(event) => {
              cancelClose();
              const touchLike = lastPressWasTouch();
              lastPointerType.current = null;
              if (open && !touchLike && canHover() && event.detail !== 0) event.preventDefault();
            }}
          >
            <Info aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </PopoverTrigger>
      </span>
      <PopoverContent
        ref={contentRef}
        id={contentId}
        aria-label={label}
        sideOffset={6}
        className="w-80 max-w-[calc(100vw-2rem)] text-sm leading-6"
        data-testid="info-tip-content"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => {
          const restoreFocus = focusWithinContent.current;
          focusWithinContent.current = false;
          suppressFocusOpen.current = restoreFocus;
          if (restoreFocus) {
            // Radix 随后同步归还焦点；微任务仅清理未被 onFocus 消费的抑制标记。
            queueMicrotask(() => {
              suppressFocusOpen.current = false;
            });
          } else {
            event.preventDefault();
          }
        }}
        onFocusCapture={() => {
          cancelClose();
          focusWithinContent.current = true;
        }}
        onBlurCapture={(event) => {
          const next = event.relatedTarget as Node | null;
          focusWithinContent.current = next
            ? (contentRef.current?.contains(next) ?? false)
            : false;
        }}
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse" && canHover()) cancelClose();
        }}
        onPointerLeave={handleHoverEnd}
        onBlur={(event) => {
          const next = event.relatedTarget as Node | null;
          if (next && contentRef.current?.contains(next)) return;
          if (!next && open) return;
          closeSoon();
        }}
      >
        {content}
      </PopoverContent>
    </Popover>
  );
}
