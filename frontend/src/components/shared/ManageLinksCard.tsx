"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

/**
 * #595 组合详情页「管理」入口卡（M1/D1）：持仓管理 / 申赎赎回记录 / 调仓交易记录 /
 * 份额变动事件 / 快照管理。由移动端原有页尾列表抽取共享，桌面端同卡新增。
 */
export default function ManageLinksCard({
  basePath,
  code,
}: {
  basePath: "/portfolio" | "/m/portfolio";
  code: string;
}) {
  const manageLinks = [
    { href: `${basePath}/${code}/positions`, label: "持仓管理" },
    { href: `${basePath}/${code}/subscriptions`, label: "申购赎回记录" },
    { href: `${basePath}/${code}/trades`, label: "调仓交易记录" },
    { href: `${basePath}/${code}/share-change-events`, label: "份额变动事件" },
    { href: `${basePath}/${code}/snapshots`, label: "快照管理" },
  ];
  return (
    <Card>
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
