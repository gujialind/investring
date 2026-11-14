"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRoleCheck } from "@/hooks/useAuth";
import LogsContent from "@/components/shared/LogsContent";
import {
  AppearanceCard,
  CalendarSyncCard,
  DataSourceCard,
  SystemInfoCard,
} from "@/components/shared/settingsSections";

/**
 * 移动设置钻取子页（#878）：/m/settings/[section]。非一级 Tab，MobileLayout 不渲染底部
 * 导航，本页自带「返回设置」出口。section 与 MobileSettingsList 的行一一对应。
 * 设置整体 admin-only（#878 验收：viewer 深链拦截）：viewer 直达任意 section 都弹回
 * 设置首页，由首页 AdminGuard 呈无权限态；非法 section 同样弹回。
 */
const SECTIONS: Record<string, { title: string }> = {
  appearance: { title: "外观与主题" },
  datasource: { title: "数据源" },
  system: { title: "系统信息" },
  calendar: { title: "交易日历同步" },
  logs: { title: "日志管理" },
};

export default function MobileSettingsSectionPage() {
  const params = useParams<{ section: string }>();
  const router = useRouter();
  const { isAdmin } = useRoleCheck();
  const section = params.section;
  const meta = SECTIONS[section];
  const forbidden = !meta || !isAdmin;

  useEffect(() => {
    if (forbidden) router.replace("/m/settings");
  }, [forbidden, router]);

  if (forbidden) return null;

  return (
    <div className="space-y-4">
      <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
        <Link href="/m/settings" aria-label="返回设置">
          <ArrowLeft className="mr-2 h-4 w-4" />
          返回设置
        </Link>
      </Button>
      <h1 className="text-2xl font-semibold">{meta.title}</h1>
      {section === "appearance" && <AppearanceCard />}
      {section === "datasource" && <DataSourceCard />}
      {section === "system" && <SystemInfoCard />}
      {section === "calendar" && <CalendarSyncCard />}
      {section === "logs" && <LogsContent variant="mobile" />}
    </div>
  );
}