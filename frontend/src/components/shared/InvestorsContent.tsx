"use client";

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
import { Plus, Pencil, Trash2, Loader2 } from "lucide-react";
import { useInvestorList, useCreateInvestor, useUpdateInvestor, useRemoveInvestor } from "@/hooks/useInvestor";
import { Investor } from "@/types/investor";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";

export default function InvestorsContent() {
  const { data, isLoading } = useInvestorList({ page_size: 100 });
  const createInvestor = useCreateInvestor();
  const updateInvestor = useUpdateInvestor();
  const removeInvestor = useRemoveInvestor();

  const investors = data?.items || [];

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingInvestor, setEditingInvestor] = useState<Investor | null>(null);
  const [pendingRemoveCode, setPendingRemoveCode] = useState<string | null>(null);
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

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">投资人管理</h1>
          <p className="text-muted-foreground">
            管理投资人和权限
          </p>
        </div>
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
      </div>

      <Card>
        <CardHeader>
          <CardTitle>投资人列表</CardTitle>
          <CardDescription>
            所有投资人的详细信息
          </CardDescription>
        </CardHeader>
        <CardContent>
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
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleEdit(investor)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(investor.code)}
                      disabled={removeInvestor.isPending}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {investors.length === 0 && (
            <div className="text-center text-muted-foreground py-8">
              暂无投资人
            </div>
          )}
        </CardContent>
      </Card>

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
