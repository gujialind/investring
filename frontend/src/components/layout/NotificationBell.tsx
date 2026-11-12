"use client";

import { Bell, CheckCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatDate } from "@/lib/utils";
import {
  useNotificationList,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
} from "@/hooks/useNotification";

// 通知级别圆点（#127 语义 token：info 与品牌蓝同源，不单独设色）
const LEVEL_DOT: Record<string, string> = {
  info: "bg-success",
  warning: "bg-warning",
  error: "bg-destructive",
};

/**
 * Navbar 通知铃铛：未读角标 + 下拉列表 + 已读/全部已读。
 * （issue：后端通知三端点与定时任务产出的通知此前无任何前端入口）
 */
export default function NotificationBell() {
  const { data } = useNotificationList({ page_size: 20 });
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();

  const notifications = data?.items || [];
  const unreadCount = notifications.filter((n) => n.status !== "read").length;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative h-8 w-8 rounded-full">
          <Bell className="h-4 w-4" />
          {unreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive-solid px-1 text-[10px] font-medium text-white">
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">通知</span>
          {unreadCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => markAllRead.mutate()}
              disabled={markAllRead.isPending}
            >
              <CheckCheck className="mr-1 h-3 w-3" />
              全部已读
            </Button>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto">
          {notifications.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">暂无通知</div>
          ) : (
            notifications.map((n) => (
              <button
                key={n.id}
                className={`flex w-full items-start gap-2 border-b px-3 py-2 text-left last:border-b-0 hover:bg-muted/50 ${
                  n.status !== "read" ? "bg-muted/30" : ""
                }`}
                onClick={() => {
                  if (n.status !== "read") markRead.mutate(n.id);
                }}
              >
                <span
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                    n.status !== "read" ? LEVEL_DOT[n.level] || LEVEL_DOT.info : "bg-transparent"
                  }`}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{n.title}</span>
                  {n.content && (
                    <span className="block truncate text-xs text-muted-foreground">{n.content}</span>
                  )}
                  {n.created_at && (
                    <span className="block text-xs text-muted-foreground">{formatDate(n.created_at)}</span>
                  )}
                </span>
              </button>
            ))
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
