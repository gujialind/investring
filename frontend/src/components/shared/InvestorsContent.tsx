"use client";

import PageHeader from "@/components/shared/PageHeader";
import { mobileExitPaths } from "@/components/shared/navItems";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Plus, Pencil, Trash2, Loader2, KeyRound } from "lucide-react";
import { useInvestorList, useCreateInvestor, useUpdateInvestor, useRemoveInvestor } from "@/hooks/useInvestor";
import { Investor } from "@/types/investor";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import QueryErrorState from "@/components/shared/QueryErrorState";
import ResetPasswordDialog from "@/components/shared/ResetPasswordDialog";
import RowActionButton from "@/components/shared/RowActionButton";
import { useAuthStore } from "@/stores/authStore";

interface InvestorsContentProps {
  variant?: "desktop" | "mobile";
}

export default function InvestorsContent({ variant = "desktop" }: InvestorsContentProps) {
  const isMobile = variant === "mobile";
  const basePath = variant === "mobile" ? "/m" : "";
  const exit = mobileExitPaths(basePath).find((e) => e.path === `${basePath}/investors`);
  const {
    data,
    isLoading,
    isError: investorsError,
    error: investorsErr,
    refetch: refetchInvestors,
  } = useInvestorList({ page_size: 100 });
  const createInvestor = useCreateInvestor();
  const updateInvestor = useUpdateInvestor();
  const removeInvestor = useRemoveInvestor();
  const currentUserCode = useAuthStore((state) => state.user?.code);

  const investors = data?.items || [];

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingInvestor, setEditingInvestor] = useState<Investor | null>(null);
  const [pendingRemoveCode, setPendingRemoveCode] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<Investor | null>(null);
  const [formData, setFormData] = useState<Partial<Investor & { password?: string }>>({
    code: "",
    name: "",
    role: "viewer",
    phone: "",
    email: "",
    password: "",
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (editingInvestor) {
      const updateData = {
        name: formData.name,
        role: formData.role,
        phone: formData.phone,
        email: formData.email,
      };
      updateInvestor.mutate({ code: editingInvestor.code, data: updateData }, {
        onSuccess: () => {
          setIsDialogOpen(false);
          setEditingInvestor(null);
          resetForm();
        },
      });
    } else {
      const createData = {
        code: formData.code || "",
        name: formData.name || "",
        role: formData.role || "viewer",
        phone: formData.phone,
        email: formData.email,
        password: formData.password || "",
      };
      createInvestor.mutate(createData, {
        onSuccess: () => {
          setIsDialogOpen(false);
          resetForm();
        },
      });
    }
  };

  const resetForm = () => {
    setFormData({ code: "", name: "", role: "viewer", phone: "", email: "", password: "" });
  };

  const handleEdit = (investor: Investor) => {
    setEditingInvestor(investor);
    setFormData({
      code: investor.code,
      name: investor.name,
      role: investor.role,
      phone: investor.phone,
      email: investor.email,
    });
    setIsDialogOpen(true);
  };

  const handleDelete = (code: string) => {
    setPendingRemoveCode(code);
  };

  return (
    <div className={isMobile ? "space-y-4" : "space-y-6"}>
      <PageHeader
        variant={variant}
        crumbs={exit ? [{ href: exit.href, label: exit.label }] : []}
        title="投资人管理"
        meta="管理投资人和权限"
        actions={
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <Button onClick={() => {
              setEditingInvestor(null);
              resetForm();
            }}>
              <Plus className="mr-2 h-4 w-4" />
              添加投资人
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{editingInvestor ? "编辑投资人" : "添加投资人"}</DialogTitle>
              <DialogDescription>
                {editingInvestor ? "修改投资人信息" : "创建新的投资人账户"}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit}>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <Label htmlFor="code">用户名</Label>
                  <Input
                    id="code"
                    value={formData.code}
                    onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                    disabled={!!editingInvestor}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="name">姓名</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    required
                  />
                </div>
                {!editingInvestor && (
                  <div className="space-y-2">
                    <Label htmlFor="password">初始密码</Label>
                    <Input
                      id="password"
                      type="password"
                      value={formData.password}
                      onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                      required={!editingInvestor}
                    />
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="role">角色</Label>
                  <select
                    id="role"
                    value={formData.role}
                    onChange={(e) => setFormData({ ...formData, role: e.target.value })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="viewer">投资人</option>
                    <option value="admin">管理员</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="phone">电话</Label>
                  <Input
                    id="phone"
                    value={formData.phone}
                    onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">邮箱</Label>
                  <Input
                    id="email"
                    type="email"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={createInvestor.isPending || updateInvestor.isPending}>
                  {(createInvestor.isPending || updateInvestor.isPending) && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  {editingInvestor ? "保存修改" : "创建投资人"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
        }
      />

      {isLoading ? (
        <div className="flex items-center justify-center h-[60vh]">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      ) : (
      <Card>
        {!isMobile && (
          <CardHeader>
            <CardTitle>投资人列表</CardTitle>
            <CardDescription>
              所有投资人的详细信息
            </CardDescription>
          </CardHeader>
        )}
        <CardContent className={isMobile ? "p-3 space-y-3" : undefined}>
          {isMobile ? (
            <div className="space-y-3" data-testid="investor-card-list">
              {investors.map((investor) => (
                <div key={investor.code} className="rounded-lg border p-3 space-y-2" data-testid="investor-card">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm" data-testid="investor-card-name">{investor.name}</span>
                      <Badge variant={investor.role === "admin" ? "default" : "neutral"}>
                        {investor.role === "admin" ? "管理员" : "投资人"}
                      </Badge>
                    </div>
                    <div className="flex shrink-0 items-center">
                      <RowActionButton
                        icon={Pencil}
                        label="编辑"
                        onClick={() => handleEdit(investor)}
                      />
                      {/* 本人行不渲染判据同桌面（#893/#898）：重置自己需旧密码，自助改密另有入口 */}
                      {investor.code !== currentUserCode && (
                        <RowActionButton
                          icon={KeyRound}
                          label="重置密码"
                          onClick={() => setResetTarget(investor)}
                        />
                      )}
                      <RowActionButton
                        icon={Trash2}
                        label="删除"
                        destructive
                        onClick={() => handleDelete(investor.code)}
                        disabled={removeInvestor.isPending}
                      />
                    </div>
                  </div>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <p className="break-all" data-testid="investor-card-code">用户名：{investor.code}</p>
                    <p>电话：{investor.phone || "--"}</p>
                    <p className="break-all">邮箱：{investor.email || "--"}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>用户名</TableHead>
                <TableHead>姓名</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>电话</TableHead>
                <TableHead>邮箱</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {investors.map((investor) => (
                <TableRow key={investor.code}>
                  <TableCell className="font-medium">{investor.code}</TableCell>
                  <TableCell>{investor.name}</TableCell>
                  <TableCell>
                    <Badge variant={investor.role === "admin" ? "default" : "neutral"}>
                      {investor.role === "admin" ? "管理员" : "投资人"}
                    </Badge>
                  </TableCell>
                  <TableCell>{investor.phone || "--"}</TableCell>
                  <TableCell>{investor.email || "--"}</TableCell>
                  <TableCell className="text-right">
                    <RowActionButton
                      icon={Pencil}
                      label="编辑"
                      onClick={() => handleEdit(investor)}
                    />
                    {/* 本人行不渲染：重置自己需旧密码（后端 400），自助改密另有入口（#893） */}
                    {investor.code !== currentUserCode && (
                      <RowActionButton
                        icon={KeyRound}
                        label="重置密码"
                        onClick={() => setResetTarget(investor)}
                      />
                    )}
                    <RowActionButton
                      icon={Trash2}
                      label="删除"
                      destructive
                      onClick={() => handleDelete(investor.code)}
                      disabled={removeInvestor.isPending}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          )}
          {/* #900 判序承重：失败必须排在「长度为 0」之前，且写外层三元——同键后台重取失败时
              react-query 保留 data（isRefetchError），嵌套形在那一态下失败块不可达（平台页 #683A 同形）。 */}
          {investorsError ? (
            <QueryErrorState error={investorsErr} onRetry={refetchInvestors} />
          ) : investors.length === 0 ? (
            <div className="text-center text-muted-foreground py-8">
              暂无投资人
            </div>
          ) : null}
        </CardContent>
      </Card>
      )}

      {resetTarget && (
        <ResetPasswordDialog
          open
          onOpenChange={(open) => !open && setResetTarget(null)}
          investor={resetTarget}
        />
      )}

      <ConfirmDialog
        open={!!pendingRemoveCode}
        onOpenChange={(open) => !open && setPendingRemoveCode(null)}
        title="移除投资人"
        description="确定要移除该投资人吗？持有份额的投资人无法移除。"
        confirmText="移除"
        onConfirm={() => {
          if (pendingRemoveCode) removeInvestor.mutate(pendingRemoveCode);
          setPendingRemoveCode(null);
        }}
      />
    </div>
  );
}
