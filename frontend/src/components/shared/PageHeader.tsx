import type { ReactNode } from "react";
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
  /** 长标题（基金名）truncate；组合名等短标题不压缩（#786） */
  truncateTitle?: boolean;
  variant?: "desktop" | "mobile";
  className?: string;
}

/**
 * 全站页头单点（#904 SPEC v1.0）：面包屑脊柱 / 标题+badge / meta / 操作槽。
 * 管理页 crumbs = navItems.entryHost 宿主（出口单源不变，布局不再渲染返回行）；
 * 操作权重三档由调用点组合：语境入口 ghost 链接档 → 次动作 outline → 主动作 filled。
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
  return (
    <div className={cn("space-y-1", className)}>
      {crumbs && crumbs.length > 0 && (
        <nav
          aria-label="面包屑"
          className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
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
            <h1
              className={cn(
                "text-2xl font-semibold",
                truncateTitle && "truncate",
              )}
            >
              {title}
            </h1>
            {badge}
          </div>
          {meta && (
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
  );
}
