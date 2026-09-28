// API barrel —— 保持 `@/lib/api` 导入路径向后兼容。
// 各模块按业务域拆分，类型统一来自 @/types/*。
export { default } from "./client";
export { ApiException, handleApiError, getErrorMessage, getBasePath } from "./client";

export { authApi } from "./auth";
export { investorApi } from "./investor";
export { portfolioApi, positionApi } from "./portfolio";
export type { CashPositionUpdateResponse, CashOverrideItem, CashOverrideListResponse } from "./portfolio";
export { subscriptionApi } from "./subscription";
export type { SubscriptionListParams } from "./subscription";
export { tradeApi } from "./trade";
export type { TradeListParams } from "./trade";
export { productApi } from "./product";
export type {
  NavAnalysisRange,
  NavCurvePoint,
  NavHistoryItem,
  NavHistoryPage,
  PriceDataPoint,
  ProductIntervalReturns,
  ProductListParams,
  ProductNavAnalysis,
} from "./product";
export { assetClassificationApi } from "./asset-classification";
export { platformApi } from "./platform";
export { systemApi } from "./system";
export { snapshotApi } from "./snapshot";
export { syncJobApi } from "./syncJob";
export { shareChangeEventApi } from "./share-change-event";
export type { ShareChangeEventListParams } from "./share-change-event";
export { logApi } from "./log";
export { taskApi } from "./task";
export { notificationApi } from "./notification";
export { cashTransferApi } from "./cash-transfer";

// 重新导出此前散落在 api.ts 中的类型，保持既有 import 兼容
export type {
  Platform,
  PlatformCreate,
  PlatformUpdate,
} from "@/types/platform";
export type {
  ShareChangeEvent,
  ShareChangeEventCreate,
  ShareChangeEventUpdate,
} from "@/types/share-change-event";
export type { SnapshotValidationCheck } from "@/types/snapshot";
export type { SyncJob } from "@/types/syncJob";
export type {
  LoginLog,
  AuditLog,
  ErrorLog,
  ScheduledTask,
  TaskExecution,
} from "@/types/log";
export type { NotificationItem } from "@/types/notification";
export type { TradingCalendarDay, DataSourceConfig } from "@/types/system";
export type {
  AssetClassificationItem,
  AssetClassificationDetail,
  AssetClassificationListResponse,
  AssetClassificationCreate,
  AssetClassificationUpdate,
  DimensionRule,
} from "@/types/asset-classification";
