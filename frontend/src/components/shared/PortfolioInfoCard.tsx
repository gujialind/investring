import type { Portfolio } from "@/types/portfolio";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatDate, getStatusBadgeVariant } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  active: "活跃",
  draft: "草稿",
  closed: "已关闭",
};

export default function PortfolioInfoCard({
  portfolio,
  variant = "desktop",
}: {
  portfolio: Portfolio;
  variant?: "desktop" | "mobile";
}) {
  const fields = [
    { label: "成立日期", value: formatDate(portfolio.started_at ?? "") },
    {
      label: "组合类型",
      value: portfolio.kind === "ledger" ? "平台市值台账型" : "净值型",
    },
    {
      label: "组合状态",
      value: STATUS_LABELS[portfolio.status] ?? portfolio.status,
      isStatus: true,
    },
    { label: "组合代码", value: portfolio.code },
  ];

  return (
    <Card data-testid="portfolio-info-card">
      <CardHeader className={variant === "mobile" ? "p-3" : undefined}>
        <CardTitle>组合口径</CardTitle>
      </CardHeader>
      <CardContent className={variant === "mobile" ? "p-3 pt-0" : undefined}>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
          {fields.map(({ label, value, isStatus }) => (
            <div key={label} className="min-w-0">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="mt-1 break-words text-sm font-medium">
                {isStatus ? (
                  <Badge variant={getStatusBadgeVariant(portfolio.status)}>
                    {value}
                  </Badge>
                ) : (
                  value
                )}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
