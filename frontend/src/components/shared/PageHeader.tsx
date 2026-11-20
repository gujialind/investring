"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PageHeaderCrumb {
  href: string;
  label: string;
  /** 可及名；缺省「返回{label}」。首 crumb 承载返回图标，钻取页祖先链可跳级 */
  ariaLabel?: string;
}

interface PageHeaderProps {
  crumbs?: PageHeaderCrumb[];
  title: ReactNode;
  /** 仅非默认态挂载（停用/草稿/关闭），语义色走 Badge variant */
  badge?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  /** 长标题（平台名等）truncate；三类产品详情已改短标题+正文身份区（#935），组合名等短标题不压缩（#786） */
  truncateTitle?: boolean;
  variant?: "desktop" | "mobile";
  className?: string;
}

/**
 * 全站页头单点（#904 SPEC v1.0）：面包屑脊柱 / 标题+badge / meta / 操作槽。
 * 管理页 crumbs = navItems.entryHost 宿主（出口单源不变，布局不再渲染返回行）；
 * 操作权重三档由调用点组合：语境入口 ghost 链接档 → 次动作 outline → 主动作 filled。
 *
 * sticky 折叠（#904 P1c）：viewport 吸顶（top-0，负 margin bleed 到 main  padding 边缘），
 * 滚动 >32px 折叠——面包屑隐去、首 crumb 收成标题行内图标返回（aria 不变）、
 * meta 隐去、出 border-b；标题恒 20px/28px（§5 页面标题档，#935），不随滚动改字号；
 * 即时切换不做尺寸动画（§14 动效词汇）。三类产品详情只传短标题「产品详情」，
 * 产品身份/管理动作在正文 ProductIdentity 区（#935 S2），折叠页眉因此只剩返回+短标题。
 */
export default function PageHeader({
  crumbs,
  title,
  badge,
  meta,
  actions,
  truncateTitle = false,
  variant = "desktop",
  className,
}: PageHeaderProps) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const onScroll = () => setCollapsed(window.scrollY > 32);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const first = crumbs?.[0];

  return (
    <div
      data-testid="page-header"
      className={cn(
        "sticky top-0 z-30 -mx-4 bg-background px-4 pb-2 pt-2 lg:-mx-8 lg:px-8",
        collapsed && "border-b",
        className,
      )}
    >
      <div className="space-y-1">
        {!collapsed && crumbs && crumbs.length > 0 && (
          <nav
            aria-label="面包屑"
            className="flex flex-wrap items-center gap-1.5 text-sm leading-5.5 text-muted-foreground"
          >
            {crumbs.map((crumb, i) => (
              <span key={crumb.href} className="flex items-center gap-1.5">
                {i > 0 && (
                  <span aria-hidden="true" className="text-border">
                    /
                  </span>
                )}
                <Link
                  href={crumb.href}
                  aria-label={crumb.ariaLabel ?? `返回${crumb.label}`}
                  className="flex items-center gap-1 hover:text-foreground"
                >
                  {i === 0 && <ArrowLeft className="h-3.5 w-3.5" />}
                  {crumb.label}
                </Link>
              </span>
            ))}
          </nav>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {collapsed && first && (
                <Link
                  href={first.href}
                  aria-label={first.ariaLabel ?? `返回${first.label}`}
                  className="-ml-2 flex shrink-0 items-center rounded-md p-1 text-muted-foreground hover:text-foreground"
                >
                  <ArrowLeft className="h-4 w-4" />
                </Link>
              )}
              <h1
                className={cn(
                  "text-xl font-semibold leading-7",
                  truncateTitle && "truncate",
                )}
              >
                {title}
              </h1>
              {badge}
            </div>
            {!collapsed && meta && (
              <p
                className={cn(
                  "text-muted-foreground",
                  variant === "mobile" ? "text-xs" : "text-sm",
                )}
              >
                {meta}
              </p>
            )}
          </div>
          {actions && (
            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
              {actions}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
