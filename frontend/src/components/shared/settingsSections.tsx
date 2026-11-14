"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Calendar,
  CheckCircle,
  ChevronRight,
  Database,
  Info,
  KeyRound,
  Loader2,
  Palette,
  RefreshCw,
  Save,
  ScrollText,
  XCircle,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getErrorMessage } from "@/lib/api";
import { useUIStore } from "@/stores/uiStore";
import { useRoleCheck } from "@/hooks/useAuth";
import { useTradingCalendar } from "@/hooks/useTradingCalendar";
import { useDataSourceConfig, useUpdateDataSource, useSyncTradingCalendar } from "@/hooks/useSystem";
import ChangePasswordDialog from "@/components/shared/ChangePasswordDialog";

/**
 * 设置页分区组件（#878）：原 SettingsContent 的六个 Tab 拆成自包含卡片，供桌面「通用」
 * Tab 堆叠与移动钻取子页（/m/settings/[section]）共用。改密独立为 ChangePasswordDialog。
 */

export function AppearanceCard() {
  // #872 PR2（D2）：三档偏好，默认 system；theme 为偏好值（非 resolvedTheme）
  const { theme, setTheme } = useTheme();
  return (
    <Card>
      <CardHeader>
        <CardTitle>外观</CardTitle>
        <CardDescription>选择浅色、深色或跟随系统</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Select value={theme ?? "system"} onValueChange={setTheme}>
          <SelectTrigger data-testid="theme-select" className="w-full sm:w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="light">浅色</SelectItem>
            <SelectItem value="dark">深色</SelectItem>
            <SelectItem value="system">跟随系统</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-sm text-muted-foreground">跟随系统将根据设备外观自动切换。</p>
      </CardContent>
    </Card>
  );
}

export function DataSourceCard() {
  const addToast = useUIStore((state) => state.addToast);
  // tushare_token 仅在用户输入时提交（服务端只返回脱敏值，不回填输入框）；akshare_enabled 回填
  const [config, setConfig] = useState({ tushare_token: "", akshare_enabled: true });
  const { data, isLoading } = useDataSourceConfig();
  const tushareConfig = data?.find((s) => s.name === "tushare");
  const akshareConfig = data?.find((s) => s.name === "akshare");
  const updateDataSource = useUpdateDataSource();

  useEffect(() => {
    if (akshareConfig) {
      setConfig((prev) => ({ ...prev, akshare_enabled: !!akshareConfig.is_enabled }));
    }
  }, [akshareConfig]);

  const handleSave = async () => {
    try {
      if (config.tushare_token.trim()) {
        await updateDataSource.mutateAsync({
          name: "tushare",
          data: { api_key: config.tushare_token.trim() },
        });
      }
      await updateDataSource.mutateAsync({
        name: "akshare",
        data: { is_enabled: config.akshare_enabled },
      });
      setConfig((prev) => ({ ...prev, tushare_token: "" }));
      addToast({ type: "success", title: "保存成功", message: "数据源配置已保存" });
    } catch {
      // 错误 toast 由 useUpdateDataSource 的 onError 统一处理
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>数据源配置</CardTitle>
        <CardDescription>配置外部数据源API</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载配置中...
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <Label htmlFor="tushare_token">Tushare Token</Label>
              <Input
                id="tushare_token"
                type="password"
                value={config.tushare_token}
                onChange={(e) => setConfig({ ...config, tushare_token: e.target.value })}
                placeholder={tushareConfig?.api_key ? "已配置 (显示脱敏值)" : "请输入Tushare API Token"}
              />
              {tushareConfig?.api_key && (
                <p className="text-xs text-muted-foreground">当前: {tushareConfig.api_key}</p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <input
                id="akshare_enabled"
                type="checkbox"
                checked={config.akshare_enabled}
                onChange={(e) => setConfig({ ...config, akshare_enabled: e.target.checked })}
                className="h-4 w-4 rounded border-input"
              />
              <Label htmlFor="akshare_enabled">启用AkShare</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              AkShare 免 Token。此开关仅用于状态展示，不控制净值同步——同步按各产品「数据源」字段路由（#880）。
            </p>
            <Button onClick={handleSave} disabled={updateDataSource.isPending}>
              {updateDataSource.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              保存配置
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function SystemInfoCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>系统信息</CardTitle>
        <CardDescription>InvestRing 系统信息</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex justify-between">
          <span className="text-muted-foreground">系统版本</span>
          <span>v{process.env.NEXT_PUBLIC_APP_VERSION}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">前端框架</span>
          <span>Next.js 16</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">后端框架</span>
          <span>FastAPI</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">数据库</span>
          <span>SQLite</span>
        </div>
      </CardContent>
    </Card>
  );
}

/** 桌面「通用」Tab 内的改密入口卡片（移动侧是分组列表里的一行按钮） */
export function PasswordCard() {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle>账户安全</CardTitle>
        <CardDescription>修改您的登录密码</CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" onClick={() => setOpen(true)}>
          <KeyRound className="mr-2 h-4 w-4" />
          修改密码
        </Button>
      </CardContent>
      <ChangePasswordDialog open={open} onOpenChange={setOpen} />
    </Card>
  );
}

export function CalendarSyncCard() {
  const [syncYear, setSyncYear] = useState(new Date().getFullYear());
  const [syncResult, setSyncResult] = useState<{
    success: boolean;
    message: string;
    synced_count?: number;
    year?: number;
  } | null>(null);
  const { data: calendarData, isLoading: calendarLoading } = useTradingCalendar(syncYear);
  const syncCalendar = useSyncTradingCalendar();

  const handleSync = () => {
    setSyncResult(null);
    syncCalendar.mutate(syncYear, {
      onSuccess: (data) => {
        setSyncResult({
          success: true,
          message: data.message,
          synced_count: data.synced_count,
          year: data.year,
        });
      },
      onError: (error: unknown) => {
        setSyncResult({ success: false, message: getErrorMessage(error, "同步失败") });
      },
    });
  };

  const tradingDays = calendarData?.filter((d) => d.is_open).length || 0;
  const restDays = (calendarData?.length || 0) - tradingDays;
  const getWeekDay = (dateStr: string) => {
    const weekDays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
    return weekDays[new Date(dateStr).getDay()];
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>交易日历同步</CardTitle>
        <CardDescription>从 Tushare 同步 A 股交易日历数据</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-end gap-4">
          <div className="space-y-2">
            <Label htmlFor="sync_year">年份</Label>
            <Input
              id="sync_year"
              type="number"
              value={syncYear}
              onChange={(e) => setSyncYear(Number(e.target.value))}
              min={2000}
              max={2100}
              className="w-32"
            />
          </div>
          <Button onClick={handleSync} disabled={syncCalendar.isPending}>
            {syncCalendar.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            同步交易日历
          </Button>
        </div>

        {syncResult && (
          <div
            className={`flex items-center gap-2 rounded-lg p-3 ${
              syncResult.success ? "bg-success-soft text-success-foreground" : "bg-destructive-soft text-destructive-foreground"
            }`}
          >
            {syncResult.success ? <CheckCircle className="h-5 w-5" /> : <XCircle className="h-5 w-5" />}
            <span>
              {syncResult.message}
              {syncResult.synced_count !== undefined && ` (新增 ${syncResult.synced_count} 条记录)`}
            </span>
          </div>
        )}

        <div className="grid grid-cols-3 gap-4">
          <Card>
            <CardContent className="pt-6">
              <div className="text-2xl font-bold">{tradingDays}</div>
              <p className="text-xs text-muted-foreground">交易日</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="text-2xl font-bold">{restDays}</div>
              <p className="text-xs text-muted-foreground">休息日</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="text-2xl font-bold">{calendarData?.length || 0}</div>
              <p className="text-xs text-muted-foreground">总天数</p>
            </CardContent>
          </Card>
        </div>

        <div>
          <h3 className="text-sm font-medium mb-2">交易日历预览</h3>
          {calendarLoading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              加载中...
            </div>
          ) : calendarData && calendarData.length > 0 ? (
            <div className="rounded-md border max-h-96 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>日期</TableHead>
                    <TableHead>星期</TableHead>
                    <TableHead>是否开盘</TableHead>
                    <TableHead>状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calendarData.map((day) => (
                    <TableRow key={day.calendar_date}>
                      <TableCell>{day.calendar_date}</TableCell>
                      <TableCell>{getWeekDay(day.calendar_date)}</TableCell>
                      <TableCell>
                        {day.is_open ? <Badge variant="success">是</Badge> : <Badge variant="neutral">否</Badge>}
                      </TableCell>
                      <TableCell>
                        {day.is_open ? (
                          <span className="text-success">交易日</span>
                        ) : (
                          <span className="text-muted-foreground">节假日/周末</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <Calendar className="h-8 w-8 mb-2" />
              <p>暂无数据，请先同步交易日历</p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** 移动分组列表的一行（Link 钻取或 button 触发弹窗），右箭头提示可进入 */
function SettingsRow({
  href,
  icon: Icon,
  label,
  onClick,
}: {
  href?: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick?: () => void;
}) {
  const inner = (
    <>
      <Icon className="h-5 w-5 shrink-0 text-muted-foreground" />
      <span className="flex-1 text-left text-sm font-medium">{label}</span>
      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
    </>
  );
  const cls = "flex w-full items-center gap-3 px-4 py-3 transition-colors hover:bg-accent";
  return href ? (
    <Link href={href} className={cls}>
      {inner}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={cls}>
      {inner}
    </button>
  );
}

/**
 * 移动端设置分组列表（#878）：通用 / 系统维护两组，行级钻取到 /m/settings/[section]
 * 子页（子页不渲染底部 Tab、自带「返回设置」）。改密为行内按钮触发弹窗，不钻取。
 * 平台/投资人管理不在此（语境分布：平台在产品页、投资人在组合页）。
 */
export function MobileSettingsList() {
  const { isAdmin } = useRoleCheck();
  const [passwordOpen, setPasswordOpen] = useState(false);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">设置</h1>
        <p className="text-muted-foreground">系统设置和配置</p>
      </div>

      <div className="space-y-2">
        <h2 className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">通用</h2>
        <Card className="divide-y overflow-hidden py-0">
          <SettingsRow href="/m/settings/appearance" icon={Palette} label="外观与主题" />
          <SettingsRow href="/m/settings/datasource" icon={Database} label="数据源" />
          <SettingsRow icon={KeyRound} label="修改密码" onClick={() => setPasswordOpen(true)} />
          <SettingsRow href="/m/settings/system" icon={Info} label="系统信息" />
        </Card>
      </div>

      <div className="space-y-2">
        <h2 className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">系统维护</h2>
        <Card className="divide-y overflow-hidden py-0">
          <SettingsRow href="/m/settings/calendar" icon={Calendar} label="交易日历同步" />
          {isAdmin && <SettingsRow href="/m/settings/logs" icon={ScrollText} label="日志管理" />}
        </Card>
      </div>

      <ChangePasswordDialog open={passwordOpen} onOpenChange={setPasswordOpen} />
    </div>
  );
}