"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Platform, PlatformCreate } from "@/lib/api";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import QueryErrorState from "@/components/shared/QueryErrorState";
import {
  usePlatformList,
  useCreatePlatform,
  useUpdatePlatform,
  useDeletePlatform,
} from "@/hooks/usePlatform";

export default function PlatformsContent() {
  const {
    data,
    isLoading,
    isError: platformsError,
    error: platformsErr,
    refetch: refetchPlatforms,
  } = usePlatformList({ page_size: 100 });

  const createPlatform = useCreatePlatform();
  const updatePlatform = useUpdatePlatform();
  const deletePlatform = useDeletePlatform();

  const platforms = data?.items || [];

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingPlatform, setEditingPlatform] = useState<Platform | null>(null);
  const [pendingDeleteCode, setPendingDeleteCode] = useState<string | null>(null);
  const [formData, setFormData] = useState<Partial<Platform>>({
    code: "",
    name: "",
    platform_type: "",
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (editingPlatform) {
      updatePlatform.mutate(
        {
          code: editingPlatform.code,
          data: {
            name: formData.name,
            platform_type: formData.platform_type,
          },
        },
        {
          onSuccess: () => {
            setIsDialogOpen(false);
            setEditingPlatform(null);
            resetForm();
          },
        }
      );
    } else {
      createPlatform.mutate(formData as PlatformCreate, {
        onSuccess: () => {
          setIsDialogOpen(false);
          resetForm();
        },
      });
    }
  };

  const resetForm = () => {
    setFormData({ code: "", name: "", platform_type: "" });
  };

  const handleEdit = (platform: Platform) => {
    setEditingPlatform(platform);
    setFormData(platform);
    setIsDialogOpen(true);
  };

  const handleDelete = (code: string) => {
    setPendingDeleteCode(code);
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
          <h1 className="text-2xl font-semibold">平台管理</h1>
          <p className="text-muted-foreground">
            管理投资平台信息
          </p>
        </div>
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <Button onClick={() => {
              setEditingPlatform(null);
              resetForm();
            }}>
              <Plus className="mr-2 h-4 w-4" />
              添加平台
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{editingPlatform ? "编辑平台" : "添加平台"}</DialogTitle>
              <DialogDescription>
                {editingPlatform ? "修改平台信息" : "创建新的平台"}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit}>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <Label htmlFor="code">平台代码</Label>
                  <Input
                    id="code"
                    value={formData.code}
                    onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                    disabled={!!editingPlatform}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="name">平台名称</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="platform_type">平台类型</Label>
                  <Input
                    id="platform_type"
                    value={formData.platform_type}
                    onChange={(e) => setFormData({ ...formData, platform_type: e.target.value })}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={createPlatform.isPending || updatePlatform.isPending}>
                  {(createPlatform.isPending || updatePlatform.isPending) && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  {editingPlatform ? "保存修改" : "创建平台"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>平台列表</CardTitle>
          <CardDescription>
            所有投资平台
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>代码</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>类型</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {platforms.map((platform) => (
                <TableRow key={platform.code}>
                  <TableCell className="font-medium">{platform.code}</TableCell>
                  <TableCell>{platform.name}</TableCell>
                  <TableCell>{platform.platform_type || "--"}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleEdit(platform)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(platform.code)}
                      disabled={deletePlatform.isPending}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {/* #683A 判序承重：失败必须排在「长度为 0」之前，否则后端故障被说成「环境里没有平台」，
              用户会去重复新建（同 code 撞 ALREADY_EXISTS）。usePlatformList 的 isError→addToast 按 #214
              保留，但 toast 几秒即消失，不能当失败的唯一出口。失败优先写在**外层三元**而非 ProductsContent
              的 `length === 0 &&` 嵌套形：同键后台重取失败时 react-query 保留 data，嵌套形在那一态下
              失败块不可达。不传 className（缺省 py-8 text-center）——该槽位的空态本就是 py-8 居中，
              传 mt-3 会让同一槽位在两态间跳尺寸（docs/design/visual-spec.md §14）。 */}
          {platformsError ? (
            <QueryErrorState error={platformsErr} onRetry={refetchPlatforms} />
          ) : platforms.length === 0 ? (
            <div className="text-center text-muted-foreground py-8">
              暂无平台
            </div>
          ) : null}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!pendingDeleteCode}
        onOpenChange={(open) => !open && setPendingDeleteCode(null)}
        title="删除平台"
        description="确定要删除该平台吗？已被持仓或交易引用的平台无法删除。"
        confirmText="删除"
        onConfirm={() => {
          if (pendingDeleteCode) deletePlatform.mutate(pendingDeleteCode);
          setPendingDeleteCode(null);
        }}
      />
    </div>
  );
}
