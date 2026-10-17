"use client";

import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { cashFlowApi, type CashFlowListParams } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";

/**
 * 现金流水列表 Hook（issue #725，纯读）。
 *
 * `keepPreviousData`：翻页/改筛选时保留上一页数据，避免整表闪空——与 useTradeList
 * 同口径。响应含 items + overview 四卡 + platform_breakdown，一次查询全部到位。
 * 页头三入口的写操作（现金转移 / 更新市值）与行级 confirm/revoke 复用各自既有
 * mutation hook，成功后失效 `queryKeys.cashFlows.root` 刷新本页（见 CashFlowsContent）。
 */
export function useCashFlowList(portfolioCode: string, params?: CashFlowListParams) {
  return useQuery({
    queryKey: queryKeys.cashFlows.list(portfolioCode, params),
    queryFn: () => cashFlowApi.list(portfolioCode, params),
    enabled: !!portfolioCode,
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });
}

/**
 * 某 (平台, 现金日期) 的**全部**流水行（详情弹窗对账块 / 「当日另有 N 笔」弹窗共用）。
 *
 * 刻意不带 flow_type / status 筛选：主列表在类型筛选态下看不到同组被隐藏的行，而弹窗
 * 要「含被当前筛选隐藏的行」（断言 8）且对账块要自证日终余额，必须取全组。上界卡到
 * 当日（from=to=cash_date），page_size 拉满 100（单平台单日不可能更多）。仅在弹窗打开
 * 时 enabled，不常驻。
 */
export function useCashFlowDay(
  portfolioCode: string,
  platformCode: string | null,
  cashDate: string | null,
  enabled: boolean
) {
  const params: CashFlowListParams | undefined =
    platformCode && cashDate
      ? { platform_code: platformCode, from_date: cashDate, to_date: cashDate, page_size: 100 }
      : undefined;
  return useQuery({
    queryKey: queryKeys.cashFlows.list(portfolioCode, { day: true, ...(params ?? {}) }),
    queryFn: () => cashFlowApi.list(portfolioCode, params),
    enabled: enabled && !!portfolioCode && !!params,
    staleTime: 30 * 1000,
  });
}
