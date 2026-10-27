"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import {
  deriveTransferGroupStatus,
  formatCurrency,
  isTransferNotReady,
  TRANSFER_GROUP_STATUS_META,
} from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { useCashTransferList, useConfirmCashTransfer } from "@/hooks/useCashTransfer";
import { useRoleCheck } from "@/hooks/useAuth";
import CancelTransferDialog from "@/components/shared/dialogs/CancelTransferDialog";
import type { CashTransferListItem } from "@/types/cash-transfer";

interface CashTransferListDialogProps {
  portfolioCode: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * 现金转移记录列表（按 transfer_group 分组）。
 * 跨天转移创建后 sell 腿当日 confirmed、buy 腿 pending（#93 非对称模型），
 * 组状态由两腿派生（issue #787）——此前只看 sell_status，跨天在途恒显「已完成」
 * 且「确认到账」按钮从不出现。撤销能力见 issue #788。
 */
export default function CashTransferListDialog({
  portfolioCode,
  open,
  onOpenChange,
}: CashTransferListDialogProps) {
  const { data, isLoading } = useCashTransferList(portfolioCode, { page_size: 50 });
  const confirmTransfer = useConfirmCashTransfer(portfolioCode);
  const { isAdmin } = useRoleCheck();
  const [cancelTarget, setCancelTarget] = useState<CashTransferListItem | null>(null);

  const transfers = data?.items || [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>现金转移记录</DialogTitle>
          <DialogDescription>
            平台间现金转移历史；跨天转移需在到账后手动确认
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            加载中...
          </div>
        ) : transfers.length === 0 ? (
          <div className="text-center text-muted-foreground py-8">暂无转移记录</div>
        ) : (
          // 窄视口（移动 390px）6 列装不下，横向滚动承接，操作列按钮才可点
          // （模式同 ProductsContent/SnapshotsContent 的表容器 overflow-x-auto）
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {/* 短文本列 nowrap：窄视口下 CJK 不被压成逐字竖排（#355 类），
                      超宽由外层 overflow-x-auto 承接横向滚动 */}
                  <TableHead className="whitespace-nowrap">转移日期</TableHead>
                  <TableHead className="whitespace-nowrap">转出 → 转入</TableHead>
                  <TableHead className="number-cell">金额</TableHead>
                  <TableHead className="whitespace-nowrap">类型</TableHead>
                  <TableHead className="whitespace-nowrap">状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {transfers.map((t) => {
                  const groupStatus = deriveTransferGroupStatus(t);
                  const statusMeta = TRANSFER_GROUP_STATUS_META[groupStatus];
                  const notReady = isTransferNotReady(t.buy_confirm_date);
                  return (
                    <TableRow key={t.transfer_group}>
                      <TableCell className="whitespace-nowrap">{t.transfer_date}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {t.from_platform} → {t.to_platform}
                      </TableCell>
                      <TableCell className="number-cell">{formatCurrency(t.amount)}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {t.cross_day ? "跨天到账" : "当天完成"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusMeta.variant}>{statusMeta.label}</Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          {groupStatus === "in_transit" && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => confirmTransfer.mutate(t.transfer_group)}
                              disabled={confirmTransfer.isPending || notReady}
                              title={
                                notReady
                                  ? `预计到账日 ${t.buy_confirm_date}，到账后可确认`
                                  : undefined
                              }
                            >
                              {confirmTransfer.isPending && (
                                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              )}
                              {notReady ? `预计 ${t.buy_confirm_date} 到账` : "确认到账"}
                            </Button>
                          )}
                          {(groupStatus === "in_transit" || groupStatus === "completed") && isAdmin && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="text-destructive"
                              onClick={() => setCancelTarget(t)}
                            >
                              撤销转移
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
        <CancelTransferDialog
          portfolioCode={portfolioCode}
          target={
            cancelTarget && {
              transfer_group: cancelTarget.transfer_group,
              from_platform: cancelTarget.from_platform,
              to_platform: cancelTarget.to_platform,
              amount: cancelTarget.amount,
              transfer_date: cancelTarget.transfer_date,
            }
          }
          open={!!cancelTarget}
          onOpenChange={(o) => {
            if (!o) setCancelTarget(null);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
