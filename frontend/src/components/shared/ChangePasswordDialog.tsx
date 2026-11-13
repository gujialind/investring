"use client";

import { useState } from "react";
import { Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useChangePassword } from "@/hooks/useAuth";
import { useUIStore } from "@/stores/uiStore";

interface ChangePasswordDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * 修改密码弹窗（#878）：原设置页「修改密码」Tab 改为弹窗，双入口共用——
 * 侧栏/移动首页账户菜单（全角色）与设置页「通用」。校验口径沿用原表单：
 * 新密码 ≥6 位、两次一致（后端无长度约束，≥6 为前端既有规则，不随本次改动放宽）。
 * 成功后 useChangePassword 会登出本人并跳登录页，这里只负责关弹窗与清空表单。
 */
export default function ChangePasswordDialog({ open, onOpenChange }: ChangePasswordDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  const changePassword = useChangePassword();
  const [form, setForm] = useState({ old_password: "", new_password: "", confirm_password: "" });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (form.new_password !== form.confirm_password) {
      addToast({ type: "error", title: "密码不一致", message: "新密码和确认密码不一致" });
      return;
    }
    if (form.new_password.length < 6) {
      addToast({ type: "error", title: "密码太短", message: "新密码长度至少 6 位" });
      return;
    }
    changePassword.mutate(
      { old_password: form.old_password, new_password: form.new_password },
      {
        onSuccess: () => {
          setForm({ old_password: "", new_password: "", confirm_password: "" });
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setForm({ old_password: "", new_password: "", confirm_password: "" });
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>修改密码</DialogTitle>
          <DialogDescription>修改您的登录密码，成功后需使用新密码重新登录</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="old_password">原密码</Label>
            <Input
              id="old_password"
              type="password"
              value={form.old_password}
              onChange={(e) => setForm({ ...form, old_password: e.target.value })}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new_password">新密码</Label>
            <Input
              id="new_password"
              type="password"
              value={form.new_password}
              onChange={(e) => setForm({ ...form, new_password: e.target.value })}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirm_password">确认新密码</Label>
            <Input
              id="confirm_password"
              type="password"
              value={form.confirm_password}
              onChange={(e) => setForm({ ...form, confirm_password: e.target.value })}
              required
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={changePassword.isPending}>
              {changePassword.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              修改密码
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}