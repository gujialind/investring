"use client";

import { cloneElement, useId, type ReactElement, type ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

type FilterTriggerProps = {
  "aria-expanded"?: boolean;
  "aria-controls"?: string;
};

interface MobileFilterSheetProps {
  trigger: ReactElement<FilterTriggerProps>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  canApply: boolean;
  error: string | null;
  onReset: () => void;
  onApply: () => void;
}

export default function MobileFilterSheet({
  trigger,
  open,
  onOpenChange,
  children,
  canApply,
  error,
  onReset,
  onApply,
}: MobileFilterSheetProps) {
  const contentId = useId();
  const triggerWithState = cloneElement(trigger, {
    "aria-expanded": open,
    "aria-controls": open ? contentId : undefined,
  });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetTrigger asChild>{triggerWithState}</SheetTrigger>
      <SheetContent id={contentId} aria-describedby={undefined}>
        <SheetHeader>
          <SheetTitle>筛选</SheetTitle>
          <SheetClose asChild>
            <Button type="button" variant="ghost" size="icon" className="h-11 w-11" aria-label="关闭">
              <X className="h-4 w-4" />
            </Button>
          </SheetClose>
        </SheetHeader>
        <SheetBody className="min-h-0 flex-1">
          {children}
          {error && (
            <p role="alert" className="mt-3 text-sm text-destructive">
              {error}
            </p>
          )}
        </SheetBody>
        <SheetFooter>
          <div className="flex items-center justify-between gap-2">
            <Button type="button" variant="ghost" className="h-11" onClick={onReset}>
              重置
            </Button>
            <div className="ml-auto flex items-center gap-2">
              <SheetClose asChild>
                <Button type="button" variant="outline" className="h-11">
                  取消
                </Button>
              </SheetClose>
              <Button type="button" className="h-11" disabled={!canApply} onClick={onApply}>
                应用筛选
              </Button>
            </div>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
