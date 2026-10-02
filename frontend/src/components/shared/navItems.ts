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
 *   该页**，而是底部 Tab 位宽有限——每项 `min-w-[3.5rem]`（56px），iPhone 13 的 390px
 *   宽放到 6 项，8~9 项（admin 全量）需 448px 以上、不可用（口径见 #650），故改走页内
 *   入口：分类在产品页、平台/任务在设置页（见 ProductsContent / SettingsContent 的
 *   `lg:hidden` 入口区块）。把取舍写成显式字段，下一个人不必再猜为什么少三项。
 * - `mobileEntryHost`：`mobileTab: false` 时，承载其页内入口的那个移动一级 Tab 路径，
 *   也就是该页的**返回目标**。这三页自身没有返回链接、底部导航也不渲染（`MobileLayout`
 *   只在一级 Tab 页显示它），不写明出口就是「可达即被困」（#650 验收第 4 条）。进 Tab 的
 *   项为 `null`。
 */
export interface NavItem {
  /** 相对 basePath 的路径，自带前导 `/`；basePath 不得带尾斜杠（见 BottomNav 的说明） */
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly: boolean;
  mobileTab: boolean;
  mobileEntryHost: string | null;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "首页", icon: LayoutDashboard, adminOnly: false, mobileTab: true, mobileEntryHost: null },
  { href: "/investors", label: "投资人", icon: Users, adminOnly: true, mobileTab: true, mobileEntryHost: null },
  { href: "/portfolio", label: "组合", icon: Briefcase, adminOnly: false, mobileTab: true, mobileEntryHost: null },
  { href: "/products", label: "产品", icon: Package, adminOnly: true, mobileTab: true, mobileEntryHost: null },
  { href: "/platforms", label: "平台", icon: Building2, adminOnly: true, mobileTab: false, mobileEntryHost: "/settings" },
  { href: "/asset-classifications", label: "分类", icon: Tags, adminOnly: true, mobileTab: false, mobileEntryHost: "/products" },
  // 注：日志页（/settings/logs）尚未实现，实现后再恢复导航项（避免死链 404）；
  // 恢复或新增项时必须同时决定 mobileTab，填 false 就得补页内入口 + 这里的返回宿主
  { href: "/settings/tasks", label: "任务", icon: PlayCircle, adminOnly: true, mobileTab: false, mobileEntryHost: "/settings" },
  { href: "/settings", label: "设置", icon: Settings, adminOnly: true, mobileTab: true, mobileEntryHost: null },
];

/** 按角色过滤后的导航项（viewer 只见非 adminOnly 项；未登录/水合前按 viewer） */
export function navItemsForRole(role: string | undefined): NavItem[] {
  return role === "admin" ? NAV_ITEMS : NAV_ITEMS.filter((item) => !item.adminOnly);
}

/** 移动端一级 Tab 的完整路径（含 basePath 前缀），供 MobileLayout 判定是否显示底部导航 */
export function mobileTabPaths(basePath: "" | "/m"): string[] {
  return NAV_ITEMS.filter((item) => item.mobileTab).map((item) => `${basePath}${item.href}`);
}

/**
 * 不进底部 Tab 的页面的站内出口（#650 验收第 4 条）：路径 → 返回到承载其页内入口的那个
 * 一级 Tab 页。宿主页不在 NAV_ITEMS 里时不产出出口，宁可不给链接也不指向不存在的路径。
 */
export function mobileExitPaths(
  basePath: "" | "/m",
): { path: string; href: string; label: string }[] {
  return NAV_ITEMS.flatMap((item) => {
    if (item.mobileTab || !item.mobileEntryHost) return [];
    const host = NAV_ITEMS.find((cand) => cand.href === item.mobileEntryHost);
    if (!host) return [];
    return [
      {
        path: `${basePath}${item.href}`,
        href: `${basePath}${host.href}`,
        label: host.label,
      },
    ];
  });
}
