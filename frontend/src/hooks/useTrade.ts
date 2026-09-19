"use client";

import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
  type QueryClient,
} from "@tanstack/react-query";
import { tradeApi, subscriptionApi, getErrorMessage, ApiException } from "@/lib/api";
import type { SubscriptionListParams, TradeListParams } from "@/lib/api";
import { TradeConfirmParams, TradeCreate, TradeUpdate } from "@/types/trade";
import { SubscriptionCreate, SubscriptionUpdate } from "@/types/subscription";
import { queryKeys } from "@/lib/queryKeys";
import type { TradePreviewOptions } from "@/lib/queryKeys";
import { useUIStore } from "@/stores/uiStore";

const TRADE_QUERY_KEY = "trades";
const SUBSCRIPTION_QUERY_KEY = "subscriptions";

/**
 * 调仓写操作后的缓存失效面（#493 §3.4.7 单点持有）：
 * 交易列表/详情、组合（含可用现金）、持仓、快照状态。
 *
 * 为什么必须连快照状态一起失效：调仓现在会改变在途资金与现金/份额的生效时点，
 * 「最新快照日 → 能否推进」的判定随写操作实时变化；未确认的到期调仓会阻断
 * 快照推进（#493 决策 6），只刷交易列表会让快照页继续显示过期结论。
 */
function invalidateTradeWrites(
  queryClient: QueryClient,
  portfolioCode: string,
  tradeId?: number
) {
  queryClient.invalidateQueries({ queryKey: queryKeys.trades.list() });
  if (tradeId) {
    queryClient.invalidateQueries({ queryKey: queryKeys.trades.detail(tradeId) });
  }
  queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.detail(portfolioCode) });
  queryClient.invalidateQueries({ queryKey: queryKeys.positions.root });
  queryClient.invalidateQueries({ queryKey: queryKeys.snapshots.root });
}

/**
 * 申赎写操作后的缓存失效面（#519）：申赎列表/详情 + 组合。
 *
 * 组合代码一律由调用方经 **mutation variables** 传入，不从响应体取：`cancel` 与
 * `unconfirm` 的后端响应只有 `{message}`（`routers/subscriptions.py`），响应里没有
 * `portfolio_code`，从 `data` 取会退化成失效键 `["portfolios", undefined]`——看着在
 * 失效，实际永不命中任何查询。与 #493 调仓侧 `invalidateTradeWrites` 同一口径。
 */
function invalidateSubscriptionWrites(
  queryClient: QueryClient,
  portfolioCode: string,
  subscriptionId: number
) {
  queryClient.invalidateQueries({ queryKey: queryKeys.subscriptions.list() });
  queryClient.invalidateQueries({ queryKey: queryKeys.subscriptions.detail(subscriptionId) });
  queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.detail(portfolioCode) });
}

// ==================== 调仓交易 Hooks ====================

// 交易列表 Hook（placeholderData 保留旧数据：筛选/翻页局部刷新不闪烁，规范 §14）
export function useTradeList(params?: TradeListParams) {
  return useQuery({
    queryKey: [TRADE_QUERY_KEY, "list", params],
    queryFn: () => tradeApi.list(params),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });
}

// 单个交易详情 Hook
export function useTrade(id: number) {
  return useQuery({
    queryKey: [TRADE_QUERY_KEY, id],
    queryFn: () => tradeApi.get(id),
    enabled: !!id && id > 0,
    staleTime: 30 * 1000,
  });
}

// 确认前预览 Hook（#248）：确认弹窗打开时才发请求；禁用重试，错误即时展示在弹窗内。
// #493：有效业务选项（确认日/价格/到账日/到账平台）纳入 query key → 用户在弹窗内改到账
// 信息即换 key 重新预览，「预览值即确认值」在新增输入维度上同样成立。
// staleTime=0 保持「重开必 refetch」，调用方须以 isLoading || isFetching 判加载态。
export function useTradePreview(
  id: number | null,
  enabled: boolean,
  options?: TradePreviewOptions
) {
  return useQuery({
    queryKey: queryKeys.trades.previewWith(id ?? 0, options),
    queryFn: () => tradeApi.preview(id!, options),
    enabled: enabled && !!id,
    retry: false,
    staleTime: 0,
  });
}

// 创建交易 Hook
export function useCreateTrade() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: TradeCreate) => tradeApi.create(data),
    onSuccess: (data) => {
      invalidateTradeWrites(queryClient, data.portfolio_code);
      addToast({
        type: "success",
        title: "交易创建成功",
        // #493：买入创建即扣款（CASH sell 腿直接 confirmed、现金日 T），基金份额仍待确认；
        // 卖出创建只建基金腿，到账平台/到账日在确认时录入
        message:
          data.trade_type === "buy"
            ? "已记账扣款，基金份额待确认"
            : "卖出申请已提交，确认时录入到账信息",
      });
    },
    onError: (error: unknown) => {
      if (error instanceof ApiException) {
        // DUPLICATE_TRADE：调用方（交易表单）会弹确认框引导 allow_duplicate 重试，此处不叠加 toast
        if (error.code === "DUPLICATE_TRADE") {
          return;
        }
        // MARKET_AMBIGUOUS：展示后端 details.available_markets，引导用户指定市场
        if (error.code === "MARKET_AMBIGUOUS") {
          const markets = (error.details?.available_markets as string[] | undefined) || [];
          addToast({
            type: "error",
            title: "市场不明确",
            message: markets.length
              ? `该代码存在多个市场：${markets.join(" / ")}，请指定市场后重试`
              : error.message,
          });
          return;
        }
      }
      addToast({
        type: "error",
        title: "交易创建失败",
        message: getErrorMessage(error, "请检查可用份额/现金是否充足"),
      });
    },
  });
}

// 更新交易 Hook
export function useUpdateTrade(id: number) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: TradeUpdate) => tradeApi.update(id, data),
    onSuccess: (data) => {
      invalidateTradeWrites(queryClient, data.portfolio_code, id);
      addToast({
        type: "success",
        title: "更新成功",
        message: "交易信息已更新",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "更新失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    },
  });
}

// 确认交易 Hook（#493：确认输入改走 query 参数，响应外层结构见 TradeConfirmResponse）
export function useConfirmTrade() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ id, params }: { id: number; params?: TradeConfirmParams }) =>
      tradeApi.confirm(id, params),
    onSuccess: (data, variables) => {
      // 交易本体在响应的 trade 字段（外层另平铺 id/portfolio_code 等历史字段）
      invalidateTradeWrites(queryClient, data.portfolio_code, variables.id);
      addToast({
        type: "success",
        title: "确认成功",
        message:
          data.trade.trade_type === "sell"
            ? `卖出已确认，资金到账日 ${data.trade.cash_confirm_date ?? data.trade.confirm_date ?? "--"}`
            : "交易已确认，基金份额已入账",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "确认失败",
        message: getErrorMessage(error, "请检查确认日期和价格"),
      });
    },
  });
}

// 取消交易 Hook（#493：取消是**整组**回退，CASH 腿一并处理）
export function useCancelTrade() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    // 响应形状见 `TradeMessageResponse`（只回 {message}，无 id/portfolio_code）：
    // 组合 code 由调用方经 variables 传入——不能从响应里取不存在的字段
    mutationFn: ({ id }: { id: number; portfolioCode: string }) => tradeApi.cancel(id),
    onSuccess: (_data, variables) => {
      invalidateTradeWrites(queryClient, variables.portfolioCode, variables.id);
      addToast({
        type: "success",
        title: "取消成功",
        message: "交易已取消",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "取消失败",
        message: getErrorMessage(error, "该交易状态不允许取消"),
      });
    },
  });
}

// 取消确认交易 Hook（#493：买入保留扣款腿、卖出删除到账腿，故失效面与写操作同宽）
export function useUnconfirmTrade() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    // 响应形状见 `TradeMessageResponse`：组合 code 取自 variables
    mutationFn: ({ id }: { id: number; portfolioCode: string }) => tradeApi.unconfirm(id),
    onSuccess: (_data, variables) => {
      invalidateTradeWrites(queryClient, variables.portfolioCode, variables.id);
      addToast({
        type: "success",
        title: "取消确认成功",
        message: "交易已取消确认，可以修改或删除",
      });
    },
    onError: (error: unknown) => {
      // SNAPSHOT_DEPENDENCY：组内任一腿生效日及之后已有快照，须先删快照（#493 组级口径）
      if (error instanceof ApiException && error.code === "SNAPSHOT_DEPENDENCY") {
        addToast({
          type: "error",
          title: "快照依赖冲突",
          message: error.message,
        });
        return;
      }
      addToast({
        type: "error",
        title: "取消确认失败",
        message: getErrorMessage(error, "操作失败，请重试"),
      });
    },
  });
}

// 删除交易 Hook
export function useDeleteTrade() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    // 响应形状见 `TradeMessageResponse`（只回 {message}，非无响应体）：组合 code 取自 variables
    mutationFn: ({ id }: { id: number; portfolioCode: string }) => tradeApi.delete(id),
    onSuccess: (_data, variables) => {
      invalidateTradeWrites(queryClient, variables.portfolioCode, variables.id);
      addToast({
        type: "success",
        title: "删除成功",
        message: "交易及其配对记录已删除",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "删除失败",
        message: getErrorMessage(error, "操作失败，请重试"),
      });
    },
  });
}

// 批量调仓 Hook
export function useBatchRebalance() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({
      portfolioCode,
      trades,
      idempotencyKey,
    }: {
      portfolioCode: string;
      trades: TradeCreate[];
      idempotencyKey?: string;
    }) => tradeApi.batchRebalance(portfolioCode, trades, idempotencyKey),
    onSuccess: (data, variables) => {
      invalidateTradeWrites(queryClient, variables.portfolioCode);
      addToast({
        type: "success",
        title: "批量调仓成功",
        message: `已创建 ${data.created_trades.length} 笔交易`,
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "批量调仓失败",
        message: getErrorMessage(error, "请检查可用现金和份额"),
      });
    },
  });
}

// ==================== 申购赎回 Hooks ====================

// 申购赎回列表 Hook（placeholderData 同 useTradeList，规范 §14）
export function useSubscriptionList(params?: SubscriptionListParams) {
  return useQuery({
    queryKey: [SUBSCRIPTION_QUERY_KEY, "list", params],
    queryFn: () => subscriptionApi.list(params),
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
  });
}

// 单个申购赎回详情 Hook
export function useSubscription(id: number) {
  return useQuery({
    queryKey: [SUBSCRIPTION_QUERY_KEY, id],
    queryFn: () => subscriptionApi.get(id),
    enabled: !!id && id > 0,
    staleTime: 30 * 1000,
  });
}

// 确认前预览 Hook（#248）：确认弹窗打开时才发请求；禁用重试，错误即时展示在弹窗内
export function useSubscriptionPreview(id: number | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.subscriptions.preview(id ?? 0),
    queryFn: () => subscriptionApi.preview(id!),
    enabled: enabled && !!id,
    retry: false,
    staleTime: 0,
  });
}

// 创建申购赎回 Hook
export function useCreateSubscription() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: SubscriptionCreate) => subscriptionApi.create(data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: [SUBSCRIPTION_QUERY_KEY, "list"] });
      queryClient.invalidateQueries({
        queryKey: ["portfolios", data.portfolio_code],
      });
      addToast({
        type: "success",
        title: "申请提交成功",
        message: `${data.sub_type === "subscribe" ? "申购" : "赎回"} 申请已提交`,
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "申请提交失败",
        message: getErrorMessage(error, "请检查输入信息"),
      });
    },
  });
}

// 更新申购赎回 Hook（issue #202：pending 编辑入口）
export function useUpdateSubscription(id: number) {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: SubscriptionUpdate) => subscriptionApi.update(id, data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: [SUBSCRIPTION_QUERY_KEY, data.id] });
      queryClient.invalidateQueries({ queryKey: [SUBSCRIPTION_QUERY_KEY, "list"] });
      // 与 create/confirm/cancel 对齐：改金额/日期后可用现金等派生值变化，失效组合缓存
      queryClient.invalidateQueries({
        queryKey: ["portfolios", data.portfolio_code],
      });
      addToast({
        type: "success",
        title: "更新成功",
        message: "申请信息已更新",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "更新失败",
        message: getErrorMessage(error, "请稍后重试"),
      });
    },
  });
}

// 确认申购赎回 Hook
export function useConfirmSubscription() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ id }: { id: number }) => subscriptionApi.confirm(id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: [SUBSCRIPTION_QUERY_KEY, data.id] });
      queryClient.invalidateQueries({ queryKey: [SUBSCRIPTION_QUERY_KEY, "list"] });
      queryClient.invalidateQueries({
        queryKey: ["portfolios", data.portfolio_code],
      });
      addToast({
        type: "success",
        title: "确认成功",
        message: `${data.sub_type === "subscribe" ? "申购" : "赎回"} 已确认`,
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "确认失败",
        message: getErrorMessage(error, "请检查确认信息"),
      });
    },
  });
}

// 取消申购赎回 Hook
export function useCancelSubscription() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ id }: { id: number; portfolioCode: string }) => subscriptionApi.cancel(id),
    onSuccess: (_data, variables) => {
      invalidateSubscriptionWrites(queryClient, variables.portfolioCode, variables.id);
      addToast({
        type: "success",
        title: "取消成功",
        message: "申请已取消",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "取消失败",
        message: getErrorMessage(error, "该申请状态不允许取消"),
      });
    },
  });
}

// 取消确认申购赎回 Hook
export function useUnconfirmSubscription() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: ({ id }: { id: number; portfolioCode: string }) => subscriptionApi.unconfirm(id),
    onSuccess: (_data, variables) => {
      invalidateSubscriptionWrites(queryClient, variables.portfolioCode, variables.id);
      // 取消确认会物理删除配对 CASH 腿并清空 shares/amount，持仓与可用现金随之变化，
      // 故连带失效 positions（#519）。快照刻意不失效：确认日及之后已有快照时本操作会被
      // SNAPSHOT_DEPENDENCY 拒绝，能成功即说明没有快照被触及。
      queryClient.invalidateQueries({ queryKey: queryKeys.positions.root });
      addToast({
        type: "success",
        title: "取消确认成功",
        message: "申购赎回事件已取消确认，可以修改或删除",
      });
    },
    onError: (error: unknown) => {
      // SNAPSHOT_DEPENDENCY: 快照已纳入该申购，需先删除快照
      if (error instanceof ApiException && error.code === "SNAPSHOT_DEPENDENCY") {
        addToast({
          type: "error",
          title: "快照依赖冲突",
          message: error.message,
        });
        return;
      }
      addToast({
        type: "error",
        title: "取消确认失败",
        message: getErrorMessage(error, "操作失败，请重试"),
      });
    },
  });
}

// 删除申购赎回 Hook（仅 pending/cancelled 可删；confirmed 需先 unconfirm）
export function useDeleteSubscription() {
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (id: number) => subscriptionApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [SUBSCRIPTION_QUERY_KEY, "list"] });
      addToast({
        type: "success",
        title: "删除成功",
        message: "申请已删除",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "删除失败",
        message: getErrorMessage(error, "已确认的申请需先取消确认"),
      });
    },
  });
}
