"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Save, Calendar, Loader2, RefreshCw, CheckCircle, XCircle } from "lucide-react";
import { getErrorMessage } from "@/lib/api";
import { useUIStore } from "@/stores/uiStore";
import { useChangePassword } from "@/hooks/useAuth";
import { useTradingCalendar } from "@/hooks/useTradingCalendar";
import { useDataSourceConfig, useUpdateDataSource, useSyncTradingCalendar } from "@/hooks/useSystem";

export default function SettingsContent() {
  const addToast = useUIStore((state) => state.addToast);
  const changePassword = useChangePassword();

  // tushare_token 仅在用户输入时提交（服务端只返回脱敏值，不回填输入框，避免脱敏串覆盖真实 token）；
  // akshare_enabled 从服务端回填
  const [dataSourceConfig, setDataSourceConfig] = useState({
    tushare_token: "",
    akshare_enabled: true,
  });

  const [passwordForm, setPasswordForm] = useState({
    old_password: "",
    new_password: "",
    confirm_password: "",
  });

  const [syncYear, setSyncYear] = useState(new Date().getFullYear());
  const [syncResult, setSyncResult] = useState<{
    success: boolean;
    message: string;
    synced_count?: number;
    year?: number;
  } | null>(null);

  const { data: calendarData, isLoading: calendarLoading } = useTradingCalendar(syncYear);

  const { data: dataSourceData, isLoading: dataSourceLoading } = useDataSourceConfig();

  // 从数组中提取数据源配置
  const tushareConfig = dataSourceData?.find((s) => s.name === "tushare");
  const akshareConfig = dataSourceData?.find((s) => s.name === "akshare");

  // 配置加载后回填 AkShare 开关，避免未改动时以默认值覆盖服务端状态
  useEffect(() => {
    if (akshareConfig) {
      setDataSourceConfig((prev) => ({ ...prev, akshare_enabled: !!akshareConfig.is_enabled }));
    }
  }, [akshareConfig]);

  const syncCalendar = useSyncTradingCalendar();

  const updateDataSource = useUpdateDataSource();

  const handleSaveDataSource = async () => {
    try {
      // tushare 与 akshare 是两个独立端点：token 仅在用户输入时提交，开关始终提交
      if (dataSourceConfig.tushare_token.trim()) {
        await updateDataSource.mutateAsync({
          name: "tushare",
          data: { api_key: dataSourceConfig.tushare_token.trim() },
        });
      }
      await updateDataSource.mutateAsync({
        name: "akshare",
        data: { is_enabled: dataSourceConfig.akshare_enabled },
      });
      setDataSourceConfig((prev) => ({ ...prev, tushare_token: "" }));
      addToast({
        type: "success",
        title: "保存成功",
        message: "数据源配置已保存",
      });
    } catch {
      // 错误 toast 由 useUpdateDataSource 的 onError 统一处理
    }
  };

  const handleChangePassword = (e: React.FormEvent) => {
    e.preventDefault();
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      addToast({
        type: "error",
        title: "密码不一致",
        message: "新密码和确认密码不一致",
      });
      return;
    }
    if (passwordForm.new_password.length < 6) {
      addToast({
        type: "error",
        title: "密码太短",
        message: "新密码长度至少 6 位",
      });
      return;
    }
    changePassword.mutate(
      {
        old_password: passwordForm.old_password,
        new_password: passwordForm.new_password,
      },
      {
        onSuccess: () => {
          setPasswordForm({ old_password: "", new_password: "", confirm_password: "" });
        },
      }
    );
  };

  const handleSyncCalendar = () => {
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
        setSyncResult({
          success: false,
          message: getErrorMessage(error, "同步失败"),
        });
      },
    });
  };

  const tradingDays = calendarData?.filter((d) => d.is_open).length || 0;
  const restDays = (calendarData?.length || 0) - tradingDays;

  const getWeekDay = (dateStr: string) => {
    const date = new Date(dateStr);
    const weekDays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
    return weekDays[date.getDay()];
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">设置</h1>
        <p className="text-muted-foreground">系统设置和配置</p>
      </div>

      <Tabs defaultValue="datasource" className="space-y-4">
        <TabsList>
          <TabsTrigger value="datasource">数据源</TabsTrigger>
          <TabsTrigger value="calendar">交易日历同步</TabsTrigger>
          <TabsTrigger value="password">修改密码</TabsTrigger>
          <TabsTrigger value="system">系统信息</TabsTrigger>
        </TabsList>

        <TabsContent value="datasource" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>数据源配置</CardTitle>
              <CardDescription>配置外部数据源API</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {dataSourceLoading ? (
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
                      value={dataSourceConfig.tushare_token}
                      onChange={(e) =>
                        setDataSourceConfig({ ...dataSourceConfig, tushare_token: e.target.value })
                      }
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
                      checked={dataSourceConfig.akshare_enabled}
                      onChange={(e) =>
                        setDataSourceConfig({ ...dataSourceConfig, akshare_enabled: e.target.checked })
                      }
                      className="h-4 w-4 rounded border-gray-300"
                    />
                    <Label htmlFor="akshare_enabled">启用AkShare</Label>
                  </div>
                  <Button onClick={handleSaveDataSource} disabled={updateDataSource.isPending}>
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
        </TabsContent>

        <TabsContent value="calendar" className="space-y-4">
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
                <Button onClick={handleSyncCalendar} disabled={syncCalendar.isPending}>
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
                  {syncResult.success ? (
                    <CheckCircle className="h-5 w-5" />
                  ) : (
                    <XCircle className="h-5 w-5" />
                  )}
                  <span>
                    {syncResult.message}
                    {syncResult.synced_count !== undefined &&
                      ` (新增 ${syncResult.synced_count} 条记录)`}
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
                              {day.is_open ? (
                                <Badge variant="success">是</Badge>
                              ) : (
                                <Badge variant="neutral">否</Badge>
                              )}
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
        </TabsContent>

        <TabsContent value="password" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>修改密码</CardTitle>
              <CardDescription>修改您的登录密码</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleChangePassword} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="old_password">原密码</Label>
                  <Input
                    id="old_password"
                    type="password"
                    value={passwordForm.old_password}
                    onChange={(e) =>
                      setPasswordForm({ ...passwordForm, old_password: e.target.value })
                    }
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new_password">新密码</Label>
                  <Input
                    id="new_password"
                    type="password"
                    value={passwordForm.new_password}
                    onChange={(e) =>
                      setPasswordForm({ ...passwordForm, new_password: e.target.value })
                    }
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="confirm_password">确认新密码</Label>
                  <Input
                    id="confirm_password"
                    type="password"
                    value={passwordForm.confirm_password}
                    onChange={(e) =>
                      setPasswordForm({ ...passwordForm, confirm_password: e.target.value })
                    }
                    required
                  />
                </div>
                <Button type="submit" disabled={changePassword.isPending}>
                  {changePassword.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="mr-2 h-4 w-4" />
                  )}
                  修改密码
                </Button>
              </form>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="system" className="space-y-4">
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
        </TabsContent>
      </Tabs>
    </div>
  );
}
