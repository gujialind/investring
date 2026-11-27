"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  ArrowRightLeft,
  CalendarClock,
  Camera,
  MoreHorizontal,
  Plus,
  Power,
  Trash2,
  Wallet,
  X,
} from "lucide-react";
import Link from "next/link";

interface PortfolioActionButtonsProps {
  portfolioCode: string;
  status: "draft" | "active" | "closed";
  kind?: string;
  basePath: string;
  variant?: "desktop" | "mobile";
  /** 控制主动作、激活/删除与快照管理入口；调用方必须按真实角色传入。 */
  isAdmin: boolean;
  onActivateClick: () => void;
  isActivatePending?: boolean;
  /** 仅 draft/closed 可见；调用方须以 isAdmin && isDeletable 门控，空壳由后端 409 判定。 */
  onDeleteClick?: (returnFocusTarget: HTMLButtonElement | null) => void;
  isDeletePending?: boolean;
}

/**
 * 组合详情操作组：普通净值型按角色/状态派生主动作与历史导航；台账不接收净值管理菜单，
 * 仅保留 admin 激活/删除。桌面 Dropdown 与移动 Sheet 共享同一菜单数据，不各自复制权限规则。
 */
export default function PortfolioActionButtons({
  portfolioCode,
  status,
  kind,
  basePath,
  variant = "desktop",
  isAdmin,
  onActivateClick,
  isActivatePending,
  onDeleteClick,
  isDeletePending,
}: PortfolioActionButtonsProps) {
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuContentRef = useRef<HTMLDivElement>(null);
  const hoverOpenedRef = useRef(false);
  const keyboardNavigatingRef = useRef(false);
  const hoverFocusRef = useRef<HTMLElement | null>(null);
  const restoringHoverFocusRef = useRef(false);
  const skipCloseFocusRef = useRef(false);
  const pendingFocusRestoreRef = useRef<HTMLElement | null>(null);
  const container = "ml-auto flex flex-wrap items-center gap-2";

  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };

  // 悬停展开的菜单收口：取消待关定时器、归还焦点到常驻触发器，再交给调用方关闭
  const closeHoverMenu = () => {
    hoverOpenedRef.current = false;
    cancelClose();
    skipCloseFocusRef.current = true;
    triggerRef.current?.focus({ preventScroll: true });
  };

  const focusFirstMenuItem = () => {
    keyboardNavigatingRef.current = true;
    queueMicrotask(() => {
      menuContentRef.current
        ?.querySelector<HTMLElement>('[role="menuitem"]:not([data-disabled])')
        ?.focus();
    });
  };

  // 点击触发器的共同意图：未开则打开；悬停打开的这次点击转为固定；已固定的再点则关闭
  const toggleFromClick = () => {
    if (!open) {
      hoverFocusRef.current = null;
      pendingFocusRestoreRef.current = null;
      setOpen(true);
    } else if (hoverOpenedRef.current) {
      hoverOpenedRef.current = false;
      cancelClose();
      focusFirstMenuItem();
    } else {
      setOpen(false);
    }
  };

  // 悬停打开的菜单离开后才延时关闭；点击/键盘打开的菜单不随鼠标离开关闭。
  // 延时期间悬停回菜单或转为固定都会 cancelClose 并清掉悬停标记，故定时器只需再查一次
  const closeSoon = () => {
    cancelClose();
    if (!hoverOpenedRef.current) return;
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      if (!hoverOpenedRef.current) return;
      if (keyboardNavigatingRef.current && menuContentRef.current?.contains(document.activeElement)) {
        return;
      }
      closeHoverMenu();
      pendingFocusRestoreRef.current = hoverFocusRef.current;
      setOpen(false);
    }, 120);
  };

  // 悬停打开时 Radix 默认把焦点移进菜单；把焦点还给打开前的元素
  useEffect(() => {
    if (!open) {
      hoverFocusRef.current = null;
      restoringHoverFocusRef.current = false;
      keyboardNavigatingRef.current = false;
      const target = pendingFocusRestoreRef.current;
      pendingFocusRestoreRef.current = null;
      if (target?.isConnected) queueMicrotask(() => target.focus({ preventScroll: true }));
      return;
    }
    if (!hoverOpenedRef.current) return;
    const target = hoverFocusRef.current;
    if (!target?.isConnected) return;
    restoringHoverFocusRef.current = true;
    queueMicrotask(() => {
      target.focus({ preventScroll: true });
      restoringHoverFocusRef.current = false;
    });
  }, [open]);

  useEffect(() => {
    const close = () => {
      cancelClose();
      hoverOpenedRef.current = false;
      setOpen(false);
    };
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("blur", close);
      cancelClose();
    };
  }, []);

  const historyLinks = [
    { href: "subscriptions", label: "申购赎回记录", icon: Plus },
    { href: "trades", label: "调仓交易记录", icon: ArrowRightLeft },
    { href: "share-change-events", label: "份额变动事件", icon: CalendarClock },
    ...(isAdmin ? [{ href: "snapshots", label: "快照管理", icon: Camera }] : []),
    { href: "cash-flows", label: "现金流水", icon: Wallet },
  ];
  // 台账无净值管理菜单；active admin 的申赎/调仓留在页头，菜单只收次动作
  const menuLinks = () => {
    if (kind === "ledger") return [];
    if (isAdmin && status === "active") {
      return historyLinks.filter(({ href }) => href !== "subscriptions" && href !== "trades");
    }
    return historyLinks;
  };
  const renderActivateButton = () => {
    if (status === "closed") {
      return (
        <Button
          variant="outline"
          className={variant === "mobile" ? "h-11 w-full" : undefined}
          onClick={onActivateClick}
          disabled={isActivatePending}
        >
          <Power className="mr-2 h-4 w-4 text-success" />
          重新激活
        </Button>
      );
    }
    if (kind === "ledger") {
      return (
        <Button
          className={variant === "mobile" ? "h-11 w-full" : undefined}
          onClick={onActivateClick}
          disabled={isActivatePending}
          data-testid="ledger-activate"
        >
          <Power className="mr-2 h-4 w-4 text-success" />
          激活组合
        </Button>
      );
    }
    if (status !== "draft") return null;
    return (
      <Link
        href={`${basePath}/${portfolioCode}/subscriptions`}
        className={variant === "mobile" ? "block w-full" : undefined}
      >
        <Button className={variant === "mobile" ? "h-11 w-full" : undefined}>
          <Plus className="mr-2 h-4 w-4" />
          首次申购激活
        </Button>
      </Link>
    );
  };
  const activateButton = renderActivateButton();

  if (variant === "desktop" && kind === "ledger") {
    if (status === "active" || !isAdmin) return null;
    return (
      <div className={container}>
        {activateButton}
        {onDeleteClick && (
          <Button
            variant="outline"
            onClick={(event) => onDeleteClick(event.currentTarget)}
            disabled={isDeletePending}
            data-testid={`portfolio-delete-${portfolioCode}`}
          >
            <Trash2 className="mr-2 h-4 w-4 text-destructive" />
            删除组合
          </Button>
        )}
      </div>
    );
  }

  if (variant === "mobile") {
    if (kind === "ledger" && (status === "active" || !isAdmin)) return null;

    return (
      <div data-testid="portfolio-action-buttons">
        <div className={isAdmin ? "grid grid-cols-[1fr_1fr_auto] gap-2" : "grid grid-cols-1 gap-2"}>
          {isAdmin && status !== "active" && <div className="col-span-2">{activateButton}</div>}
          {isAdmin && status === "active" && (
            <>
              <Link href={`${basePath}/${portfolioCode}/trades`}>
                <Button variant="outline" className="h-11 w-full">
                  <ArrowRightLeft className="mr-2 h-4 w-4" />
                  调仓
                </Button>
              </Link>
              <Link href={`${basePath}/${portfolioCode}/subscriptions`}>
                <Button className="h-11 w-full">
                  <Plus className="mr-2 h-4 w-4" />
                  申购赎回
                </Button>
              </Link>
            </>
          )}
          <Sheet>
            <SheetTrigger asChild>
              <Button
                ref={triggerRef}
                variant="outline"
                size={isAdmin ? "icon" : "default"}
                className={isAdmin ? "h-11 w-11" : "h-11 w-full"}
                aria-label="更多操作"
                data-testid="portfolio-more-trigger"
              >
                <MoreHorizontal className={isAdmin ? "h-4 w-4" : "mr-2 h-4 w-4"} />
                {!isAdmin && "更多"}
              </Button>
            </SheetTrigger>
            <SheetContent data-testid="portfolio-more-menu">
              <SheetHeader>
                <SheetTitle>更多操作</SheetTitle>
                <SheetClose asChild>
                  <Button variant="ghost" size="icon" className="h-11 w-11" aria-label="关闭">
                    <X className="h-4 w-4" />
                  </Button>
                </SheetClose>
              </SheetHeader>
              <SheetBody className="space-y-1">
                {menuLinks().map(({ href, label, icon: Icon }) => (
                  <SheetClose asChild key={href}>
                    <Link
                      href={`${basePath}/${portfolioCode}/${href}`}
                      className="flex h-11 items-center gap-2 text-sm"
                    >
                      <Icon className="h-4 w-4 text-muted-foreground" />
                      {label}
                    </Link>
                  </SheetClose>
                ))}
                {onDeleteClick && (
                  <div className="mt-2 border-t pt-2">
                    <p className="px-2 py-1 text-xs font-medium text-muted-foreground">危险操作</p>
                    <SheetClose asChild>
                      <button
                        type="button"
                        onClick={() => onDeleteClick(triggerRef.current)}
                        disabled={isDeletePending}
                        data-testid={`portfolio-delete-${portfolioCode}`}
                        className="flex h-11 w-full items-center gap-2 text-sm text-destructive disabled:opacity-50"
                      >
                        <Trash2 className="h-4 w-4" />
                        删除组合
                      </button>
                    </SheetClose>
                  </div>
                )}
              </SheetBody>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    );
  }

  return (
    <div className={container}>
      {isAdmin && status === "active" && (
        <>
          <Link href={`${basePath}/${portfolioCode}/trades`}>
            <Button variant="outline">
              <ArrowRightLeft className="mr-2 h-4 w-4" />
              调仓
            </Button>
          </Link>
          <Link href={`${basePath}/${portfolioCode}/subscriptions`}>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              申购赎回
            </Button>
          </Link>
        </>
      )}
      {isAdmin && status !== "active" && activateButton}
      <DropdownMenu
        modal={false}
        open={open}
        onOpenChange={(next) => {
          cancelClose();
          if (next) {
            skipCloseFocusRef.current = false;
            hoverFocusRef.current = null;
            keyboardNavigatingRef.current = false;
            pendingFocusRestoreRef.current = null;
          } else {
            hoverOpenedRef.current = false;
          }
          setOpen(next);
        }}
      >
        <DropdownMenuTrigger asChild>
          <Button
            ref={triggerRef}
            variant="outline"
            aria-label="更多操作"
            data-testid="portfolio-more-trigger"
            onPointerDown={(event) => {
              if (event.button !== 0 || event.ctrlKey) return;
              // 接管左键点击，避免 Radix 默认 toggle 关闭刚悬停打开的菜单
              event.preventDefault();
              toggleFromClick();
            }}
            onPointerEnter={(event) => {
              // 触屏/笔不触发 hover 展开，但同样不该被上一次离开的延时关闭误伤
              cancelClose();
              if (event.pointerType !== "mouse") return;
              if (!open) {
                hoverOpenedRef.current = true;
                pendingFocusRestoreRef.current = null;
                hoverFocusRef.current =
                  document.activeElement instanceof HTMLElement ? document.activeElement : null;
              }
              setOpen(true);
            }}
            onPointerLeave={closeSoon}
            onKeyDown={(event) => {
              // Enter/Space 交由 Radix 合成 click = detail 0
              if (event.key !== "ArrowDown" || !open) return;
              event.preventDefault();
              focusFirstMenuItem();
            }}
            onClick={(event) => {
              if (event.detail !== 0) return;
              event.preventDefault();
              toggleFromClick();
            }}
          >
            <MoreHorizontal className="mr-2 h-4 w-4" />
            更多
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          ref={menuContentRef}
          align="end"
          className="w-56"
          data-testid="portfolio-more-menu"
          onCloseAutoFocus={(event) => {
            if (!skipCloseFocusRef.current) return;
            skipCloseFocusRef.current = false;
            event.preventDefault();
          }}
          onFocusOutside={(event) => {
            if (!restoringHoverFocusRef.current) return;
            restoringHoverFocusRef.current = false;
            event.preventDefault();
          }}
          onPointerDownOutside={(event) => {
            // 放行触发器 pointerdown，由组件显式决定固定或关闭
            const original = event.detail.originalEvent;
            if (original.button !== 0 || original.ctrlKey || !triggerRef.current) return;
            if (event.composedPath().includes(triggerRef.current)) event.preventDefault();
          }}
          onPointerEnter={cancelClose}
          onPointerLeave={closeSoon}
          onKeyDown={(event) => {
            if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
              keyboardNavigatingRef.current = true;
            }
          }}
        >
          {menuLinks().map(({ href, label, icon: Icon }) => (
            <DropdownMenuItem asChild key={href} className="h-11">
              <Link href={`${basePath}/${portfolioCode}/${href}`}>
                <Icon className="mr-2 h-4 w-4 text-muted-foreground" />
                {label}
              </Link>
            </DropdownMenuItem>
          ))}
          {onDeleteClick && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-xs text-muted-foreground">危险操作</DropdownMenuLabel>
              <DropdownMenuItem
                className="h-11 text-destructive"
                disabled={isDeletePending}
                onSelect={(event) => {
                  // 拦掉 Radix 的返回焦点，让 AlertDialog 自己接管；先收口悬停态并把焦点
                  // 交还常驻触发器，删除弹窗才能在菜单卸载后正常接管焦点
                  event.preventDefault();
                  closeHoverMenu();
                  setOpen(false);
                  onDeleteClick(triggerRef.current);
                }}
                data-testid={`portfolio-delete-${portfolioCode}`}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                删除组合
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
