"use client";

import { useState } from "react";
import { Loader2, KeyRound } from "lucide-react";
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
import { useResetPassword } from "@/hooks/useAuth";
import { useUIStore } from "@/stores/uiStore";

interface ResetPasswordDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  investor: { code: string; name: string };
}

/**
 * 管理员重置他人密码弹窗（#893）：形态对齐 ChangePasswordDialog（#878），
 * 仅新密码 + 确认（≥6 位、两次一致），无原密码字段。成功后只关弹窗清表单——
 * 后端仅撤销目标会话，操作者不登出、不跳转。
 */
export default function ResetPasswordDialog({ open, onOpenChange, investor }: ResetPasswordDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  const resetPassword = useResetPassword();
  const [form, setForm] = useState({ new_password: "", confirm_password: "" });

  // 提交进行中禁止关闭，避免「取消」被误解为撤销已提交的重置（mutation 不可中止）。
  const requestClose = () => {
    if (resetPassword.isPending) return;
    setForm({ new_password: "", confirm_password: "" });
    onOpenChange(false);
  };

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
    resetPassword.mutate(
      { target_code: investor.code, new_password: form.new_password },
      {
        onSuccess: () => {
          setForm({ new_password: "", confirm_password: "" });
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else requestClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重置密码</DialogTitle>
          <DialogDescription>
            为 {investor.name}（{investor.code}）设置新密码。重置后该用户所有登录立即失效，请通过安全渠道告知新密码。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="reset_new_password">新密码</Label>
            <Input
              id="reset_new_password"
              type="password"
              value={form.new_password}
              onChange={(e) => setForm({ ...form, new_password: e.target.value })}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="reset_confirm_password">确认新密码</Label>
            <Input
              id="reset_confirm_password"
              type="password"
              value={form.confirm_password}
              onChange={(e) => setForm({ ...form, confirm_password: e.target.value })}
              required
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={requestClose} disabled={resetPassword.isPending}>
              取消
            </Button>
            <Button type="submit" disabled={resetPassword.isPending}>
              {resetPassword.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <KeyRound className="mr-2 h-4 w-4" />
              )}
              重置密码
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}