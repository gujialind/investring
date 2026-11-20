"use client";

import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useSnapshotStatus } from "@/hooks/useSnapshot";

/**
 * 快照停更可见提示（issue #906 SPEC §4，双端共享）。
 *
 * 消费 status 端点的 trailing 缺口 catchup_pending_dates：最新快照日之后至
 * 业务昨日应生成而未生成的 SSE 交易日。非空 → 渲染；正常追平 / 台账型 /
 * 无快照基线（draft）恒空 → 不渲染。修复 A 把悬空产品码挡在 MISSING_NAV 后，
 * 读侧停在最后一条成功快照——本提示让「数字停更」被动可见，不必只靠用户察觉。
 *
 * 文案中性（SPEC §4）：手动快照组合（auto_snapshot_enabled=false）也可能有
 * trailing gap，只陈述停更事实并引导，不预设一定是故障；精确到悬空产品码的
 * 「最近失败原因」字段留 follow-up。
 */
export default function SnapshotCatchupAlert({ portfolioCode }: { portfolioCode: string }) {
  const { data: status } = useSnapshotStatus(portfolioCode);
  const pending = status?.catchup_pending_dates ?? [];
  if (pending.length === 0) return null;

  return (
    <Alert variant="destructive" data-testid="snapshot-catchup-alert">
      <AlertTriangle className="h-4 w-4" />
      <AlertDescription>
        快照停更：最新快照 {status?.latest_snapshot_date ?? "--"}，{pending.length}{" "}
        个交易日未生成（可能因取价失败/产品行缺失被挡），请查任务执行日志或尝试重算
      </AlertDescription>
    </Alert>
  );
}
