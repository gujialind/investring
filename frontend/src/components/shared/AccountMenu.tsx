"use client";

import { useState } from "react";
import { ChevronUp, KeyRound, LogOut, User } from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import { useLogout } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import ChangePasswordDialog from "@/components/shared/ChangePasswordDialog";

interface AccountMenuProps {
  /** 侧栏收起态：仅头像，不显示姓名/角色 */
  collapsed?: boolean;
  /** 弹层对齐：侧栏展开 start、收起 center、移动首页 end */
  align?: "start" | "center" | "end";
  className?: string;
}

/**
 * 账户菜单（#878）：头像 + 姓名/角色触发器，向上展开，含「修改密码」（弹窗）与
 * 「退出登录」。全角色可用——补上移动端此前无退出入口的缺口。侧栏底部与移动首页
 * 账户区共用同一份；触发器 aria-label 恒为「用户菜单」（E2E 锚点）。
 */
export default function AccountMenu({ collapsed = false, align = "start", className }: AccountMenuProps) {
  const { user } = useAuthStore();
  const handleLogout = useLogout();
  const [passwordOpen, setPasswordOpen] = useState(false);
  const roleLabel = user?.role === "admin" ? "管理员" : "投资人";

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            aria-label="用户菜单"
            className={cn(
              "h-auto rounded-lg",
              collapsed ? "w-9 justify-center p-1" : "w-full justify-start gap-2 px-2 py-1.5",
              className,
            )}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <User className="h-4 w-4" />
            </span>
            {!collapsed && (
              <>
                <span className="flex min-w-0 flex-1 flex-col items-start leading-tight">
                  <span className="w-full truncate text-sm font-medium">{user?.name}</span>
                  <span className="w-full truncate text-xs text-muted-foreground">{roleLabel}</span>
                </span>
                <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
              </>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align={align} className="w-56">
          <div className="flex items-center gap-2 p-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <User className="h-4 w-4" />
            </span>
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-medium">{user?.name}</span>
              <span className="truncate text-xs text-muted-foreground">{roleLabel}</span>
            </div>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setPasswordOpen(true)}>
            <KeyRound className="mr-2 h-4 w-4" />
            修改密码
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => handleLogout()}>
            <LogOut className="mr-2 h-4 w-4" />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangePasswordDialog open={passwordOpen} onOpenChange={setPasswordOpen} />
    </>
  );
}