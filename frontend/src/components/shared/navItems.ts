import {
  LayoutDashboard,
  Users,
  Briefcase,
  Package,
  Building2,
  Settings,
  PlayCircle,
  Tags,
  type LucideIcon,
} from "lucide-react";

/**
 * 全站导航单源（#650）：Sidebar（桌面宽屏）、共享 BottomNav（桌面窄屏 + /m 一级 Tab）
 * 都从这份数据派生，取代原先三份各自维护的列表（layout/Sidebar、layout/MobileNav、
 * mobile/BottomNav）——改一项导航只改这里。
 *
 * - `adminOnly`：viewer 角色不出的项（viewer 只见首页/组合）。
 * - `mobileTab`：是否进移动端底部 Tab。false 的项（平台/分类/任务）**不是移动端没有
 *   该页**，而是底部 Tab 位宽有限（5 项已占满 iPhone 13 的 390px），改走页内入口：
 *   分类在产品页、平台/任务在设置页（见 ProductsContent / SettingsContent 的
 *   `lg:hidden` 入口区块）。把取舍写成显式字段，下一个人不必再猜为什么少三项。
 */
export interface NavItem {
  /** 相对 basePath 的路径：桌面 basePath="/"、移动 "/m" */
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly: boolean;
  mobileTab: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "首页", icon: LayoutDashboard, adminOnly: false, mobileTab: true },
  { href: "/investors", label: "投资人", icon: Users, adminOnly: true, mobileTab: true },
  { href: "/portfolio", label: "组合", icon: Briefcase, adminOnly: false, mobileTab: true },
  { href: "/products", label: "产品", icon: Package, adminOnly: true, mobileTab: true },
  { href: "/platforms", label: "平台", icon: Building2, adminOnly: true, mobileTab: false },
  { href: "/asset-classifications", label: "分类", icon: Tags, adminOnly: true, mobileTab: false },
  // 注：日志页（/settings/logs）尚未实现，实现后再恢复导航项（避免死链 404）
  { href: "/settings/tasks", label: "任务", icon: PlayCircle, adminOnly: true, mobileTab: false },
  { href: "/settings", label: "设置", icon: Settings, adminOnly: true, mobileTab: true },
];

/** 按角色过滤后的导航项（viewer 只见非 adminOnly 项；未登录/水合前按 viewer） */
export function navItemsForRole(role: string | undefined): NavItem[] {
  return role === "admin" ? NAV_ITEMS : NAV_ITEMS.filter((item) => !item.adminOnly);
}

/** 移动端一级 Tab 的完整路径（含 basePath 前缀），供 MobileLayout 判定是否显示底部导航 */
export function mobileTabPaths(basePath: string): string[] {
  return NAV_ITEMS.filter((item) => item.mobileTab).map((item) => `${basePath}${item.href}`);
}
