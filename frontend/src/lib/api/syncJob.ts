import { request } from "./client";
import { SyncJob, DataSourceSwitchResult } from "@/types/syncJob";

export const syncJobApi = {
  // 查询后台任务状态（#146 重算进度轮询）
  get: (jobId: number) =>
    request<SyncJob>({
      method: "GET",
      url: `/sync-jobs/${jobId}`,
    }),

  // 逐产品明细（#882 批量切换台账：data_source_switch 类型返回 params["results"]；
  // 价格同步类型返回 NavSyncDetail 行，前端当前不消费该形态）
  details: (jobId: number) =>
    request<{ job: SyncJob; details: DataSourceSwitchResult[] }>({
      method: "GET",
      url: `/sync-jobs/${jobId}/details`,
    }),
};
