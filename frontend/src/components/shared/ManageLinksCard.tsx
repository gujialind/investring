"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { useRoleCheck } from "@/hooks/useAuth";

/**
 * #595 组合详情页「管理」入口卡（M1/D1）：持仓管理 / 申赎赎回记录 / 调仓交易记录 /
 * 份额变动事件 / 快照管理 / 现金流水（#725）。由移动端原有页尾列表抽取共享，桌面端同卡新增。
 * #786 部分反转 #595：事件/快照上移操作区后本卡按场景去重——active+admin 移除两入口；
 * active+viewer 移除指向 AdminGuard 页的快照死入口、保留事件（事件读接口 viewer 可达）；
 * draft/closed 全量保留不丢入口。去重仅经 status 参数 + 角色派生，链接数组单一来源。
 */
export default function ManageLinksCard({
  basePath,
  code,
  status,
}: {
  basePath: "/portfolio" | "/m/portfolio";
  code: string;
  status: "draft" | "active" | "closed";
}) {
  const { isAdmin } = useRoleCheck();
  const isActive = status === "active";
  const manageLinks = [
    { href: `${basePath}/${code}/positions`, label: "持仓管理" },
    { href: `${basePath}/${code}/subscriptions`, label: "申购赎回记录" },
    { href: `${basePath}/${code}/trades`, label: "调仓交易记录" },
    // #786：active+admin 已上移页头/操作区去重；active+viewer 保留（事件列表 viewer 可读）
    ...(isActive && isAdmin
      ? []
      : [{ href: `${basePath}/${code}/share-change-events`, label: "份额变动事件" }]),
    // #786：快照页有 AdminGuard，viewer 入口是死入口故 active 即隐藏；admin 上移后去重
    ...(isActive ? [] : [{ href: `${basePath}/${code}/snapshots`, label: "快照管理" }]),
    { href: `${basePath}/${code}/cash-flows`, label: "现金流水" },
  ];
  return (
    <Card data-testid="manage-links-card">
      <CardContent className="p-0">
        <h3 className="px-4 pb-1 pt-4 text-sm font-medium text-muted-foreground">
          管理
        </h3>
        <div className="divide-y">
          {manageLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="flex items-center justify-between px-4 py-3 text-sm"
            >
              {link.label}
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
