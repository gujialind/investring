"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

interface InfoTipProps {
  label: string;
  content: string;
}

export default function InfoTip({ label, content }: InfoTipProps) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const suppressFocusOpen = useRef(false);
  const focusWithinContent = useRef(false);

  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };

  const closeSoon = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      setOpen(false);
    }, 120);
  };

  const canHover = () => window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  useEffect(
    () => () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
    },
    [],
  );

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        cancelClose();
        setOpen(next);
      }}
    >
      <span className="relative mx-3 inline-flex h-5 w-5 shrink-0 items-center justify-center">
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={label}
            aria-expanded={open}
            aria-controls={open ? contentId : undefined}
            aria-describedby={open ? contentId : undefined}
            className="absolute inline-flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            onPointerDown={() => {
              suppressFocusOpen.current = false;
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
            onPointerLeave={(event) => {
              if (event.pointerType === "mouse" && canHover()) closeSoon();
            }}
            onFocus={(event) => {
              if (suppressFocusOpen.current) {
                suppressFocusOpen.current = false;
                return;
              }
              if (event.currentTarget.matches(":focus-visible")) {
                cancelClose();
                setOpen(true);
              }
            }}
            onBlur={(event) => {
              const next = event.relatedTarget as Node | null;
              if (next && contentRef.current?.contains(next)) return;
              if (!next && open) return;
              closeSoon();
            }}
            onClick={cancelClose}
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
          focusWithinContent.current = true;
        }}
        onBlurCapture={(event) => {
          const next = event.relatedTarget as Node | null;
          if (next) focusWithinContent.current = contentRef.current?.contains(next) ?? false;
        }}
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse" && canHover()) cancelClose();
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse" && canHover()) closeSoon();
        }}
        onBlur={closeSoon}
      >
        {content}
      </PopoverContent>
    </Popover>
  );
}
