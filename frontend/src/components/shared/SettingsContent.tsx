"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRoleCheck } from "@/hooks/useAuth";
import LogsContent from "@/components/shared/LogsContent";
import {
  AppearanceCard,
  CalendarSyncCard,
  DataSourceCard,
  PasswordCard,
  SystemInfoCard,
} from "@/components/shared/settingsSections";

/**
 * 桌面设置页（#878）：纯系统设置，三 Tab——通用（外观/账户安全/数据源/系统信息合并）、
 * 交易日历同步、日志管理（admin）。原「修改密码」Tab 改为弹窗（PasswordCard 触发），
 * 平台/投资人管理移出设置（语境分布：平台在产品页、投资人在组合页）。
 * 移动端走分组列表 + 钻取子页（MobileSettingsList / app/m/settings/[section]），不用本组件。
 */
export default function SettingsContent() {
  const { isAdmin } = useRoleCheck();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">设置</h1>
        <p className="text-muted-foreground">系统设置和配置</p>
      </div>

      <Tabs defaultValue="general" className="space-y-4">
        <TabsList>
          <TabsTrigger value="general">通用</TabsTrigger>
          <TabsTrigger value="calendar">交易日历同步</TabsTrigger>
          {/* 日志管理 admin-only（#715）：水合前 useRoleCheck 按非 admin，Tab 不渲染 */}
          {isAdmin && <TabsTrigger value="logs">日志管理</TabsTrigger>}
        </TabsList>

        <TabsContent value="general" className="space-y-4">
          <AppearanceCard />
          <PasswordCard />
          <DataSourceCard />
          <SystemInfoCard />
        </TabsContent>

        <TabsContent value="calendar" className="space-y-4">
          <CalendarSyncCard />
        </TabsContent>

        {isAdmin && (
          <TabsContent value="logs" className="space-y-4">
            <LogsContent variant="desktop" />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}