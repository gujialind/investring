import { request } from "./client";
import {
  LedgerEntry,
  LedgerEntryCreate,
  LedgerEntryUpdate,
  LedgerFlow,
  LedgerFlowCreate,
  LedgerFlowUpdate,
  LedgerFlowWriteResult,
  LedgerTotals,
} from "@/types/ledger";

// 平台市值台账型组合（issue #905 PR3）API。
// 端点与后端 routers/ledgers.py 逐条对齐（挂 /api/portfolios/{code}/ledger/*）；
// 写侧 admin、读侧登录用户（S8），后端 403/422 兜底，前端按角色不渲染写入口。
export const ledgerApi = {
  // ---- 市值台账 ledger_entry ----
  listEntries: (code: string, params?: { platform_code?: string }) =>
    request<LedgerEntry[]>({ method: "GET", url: `/portfolios/${code}/ledger/entries`, params }),

  createEntry: (code: string, data: LedgerEntryCreate) =>
    request<LedgerEntry>({ method: "POST", url: `/portfolios/${code}/ledger/entries`, data }),

  updateEntry: (code: string, entryId: number, data: LedgerEntryUpdate) =>
    request<LedgerEntry>({ method: "PUT", url: `/portfolios/${code}/ledger/entries/${entryId}`, data }),

  deleteEntry: (code: string, entryId: number) =>
    request<{ message: string }>({ method: "DELETE", url: `/portfolios/${code}/ledger/entries/${entryId}` }),

  // ---- 本金进出 ledger_flow ----
  listFlows: (code: string, params?: { platform_code?: string }) =>
    request<LedgerFlow[]>({ method: "GET", url: `/portfolios/${code}/ledger/flows`, params }),

  createFlow: (code: string, data: LedgerFlowCreate) =>
    request<LedgerFlowWriteResult>({ method: "POST", url: `/portfolios/${code}/ledger/flows`, data }),

  updateFlow: (code: string, flowId: number, data: LedgerFlowUpdate) =>
    request<LedgerFlowWriteResult>({ method: "PUT", url: `/portfolios/${code}/ledger/flows/${flowId}`, data }),

  deleteFlow: (code: string, flowId: number) =>
    request<{ message: string }>({ method: "DELETE", url: `/portfolios/${code}/ledger/flows/${flowId}` }),

  // ---- 收益汇总 ----
  getTotals: (code: string, params?: { as_of_date?: string }) =>
    request<LedgerTotals>({ method: "GET", url: `/portfolios/${code}/ledger/totals`, params }),
};