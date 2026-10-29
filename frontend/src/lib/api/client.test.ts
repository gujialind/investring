import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { AxiosError, type InternalAxiosRequestConfig } from "axios";
import api, { ApiException, getErrorMessage, handleApiError, request } from "./client";
import { useAuthStore } from "@/stores/authStore";

// #643：client.ts 的 detail 三分支解析（string / 结构化对象 / FastAPI 校验数组）。
// 本文件在覆盖率分母外（vitest.config.ts 显式排除 src/lib/api/**），测试会执行但不计阈值。

function axiosErrorWithDetail(detail: unknown, status = 422): AxiosError {
  const error = new Error(
    `Request failed with status code ${status}`
  ) as AxiosError;
  error.isAxiosError = true;
  error.response = { status, data: { detail } } as never;
  return error;
}

describe("handleApiError", () => {
  it("裸字符串 detail → UNKNOWN_ERROR + 原文", () => {
    const result = handleApiError(axiosErrorWithDetail("Not Found", 404));
    expect(result.code).toBe("UNKNOWN_ERROR");
    expect(result.message).toBe("Not Found");
    expect(result.status).toBe(404);
  });

  it("结构化对象 detail → code/message/details 透传", () => {
    const result = handleApiError(
      axiosErrorWithDetail({
        error: "CASH_TRADE_FORBIDDEN",
        message: "现金产品不允许直接创建调仓交易",
        details: { platform: "HBZQ" },
      })
    );
    expect(result.code).toBe("CASH_TRADE_FORBIDDEN");
    expect(result.message).toBe("现金产品不允许直接创建调仓交易");
    expect(result.details).toEqual({ platform: "HBZQ" });
  });

  it("校验数组 detail → VALIDATION_ERROR + 字段级拼接文案（#643）", () => {
    const result = handleApiError(
      axiosErrorWithDetail([
        {
          loc: ["body", "amount"],
          msg: "确保此值不大于 999999999999.99",
          type: "less_than_equal",
        },
      ])
    );
    expect(result.code).toBe("VALIDATION_ERROR");
    expect(result.message).toBe("amount: 确保此值不大于 999999999999.99");
    expect(result.message).not.toContain("Request failed with status code");
  });

  it("校验数组多条 → 分号拼接", () => {
    const result = handleApiError(
      axiosErrorWithDetail([
        { loc: ["body", "amount"], msg: "确保此值大于等于 0" },
        { loc: ["body", "platform_code"], msg: "字段必填" },
      ])
    );
    expect(result.message).toBe("amount: 确保此值大于等于 0; platform_code: 字段必填");
  });

  it("校验条目缺 loc / 缺 msg → 退化到可用部分，不抛错（#655 L2 S1）", () => {
    expect(handleApiError(axiosErrorWithDetail([{ msg: "字段必填" }])).message).toBe(
      "字段必填"
    );
    expect(
      handleApiError(axiosErrorWithDetail([{ loc: ["body", "amount"] }])).message
    ).toBe("amount");
  });

  it("空校验数组 → VALIDATION_ERROR + 「请求失败」，不露裸 HTTP 文案（#655 L2 S1）", () => {
    const result = handleApiError(axiosErrorWithDetail([]));
    expect(result.code).toBe("VALIDATION_ERROR");
    expect(result.message).toBe("请求失败");
  });

  it("有响应但 detail 解析不出 → 「请求失败」；无响应 → axios 原文（#655 L2 S2）", () => {
    expect(handleApiError(axiosErrorWithDetail({})).message).toBe("请求失败");
    const network = new Error("Network Error") as AxiosError;
    network.isAxiosError = true;
    expect(handleApiError(network).message).toBe("Network Error");
  });
});

describe("getErrorMessage", () => {
  it("ApiException → 其 message（含校验拼接文案）", () => {
    const wrapped = handleApiError(
      axiosErrorWithDetail([{ loc: ["body", "shares"], msg: "确保此值大于 0" }])
    );
    expect(getErrorMessage(wrapped)).toBe("shares: 确保此值大于 0");
  });

  it("裸 axios 校验数组错误 → 拼接文案而非裸 HTTP 文案（#643）", () => {
    const raw = axiosErrorWithDetail([
      { loc: ["body", "amount"], msg: "确保此值不大于 999999999999.99" },
    ]);
    expect(getErrorMessage(raw)).toBe("amount: 确保此值不大于 999999999999.99");
  });

  it("裸 axios 结构化对象错误 → detail.message", () => {
    const raw = axiosErrorWithDetail({ error: "NON_TRADING_DAY", message: "非交易日" });
    expect(getErrorMessage(raw)).toBe("非交易日");
  });

  it("无法解析 → fallback", () => {
    expect(getErrorMessage({ response: { data: {} } }, "请检查输入")).toBe(
      "请检查输入"
    );
  });

  it("有响应但 detail 解析不出 → 调用方 fallback，不露裸 HTTP 文案（#655 L2 S2）", () => {
    expect(getErrorMessage(axiosErrorWithDetail([]), "请刷新重试")).toBe("请刷新重试");
    expect(getErrorMessage(axiosErrorWithDetail({}), "请刷新重试")).toBe("请刷新重试");
  });

  it("无响应（网络错误）→ axios 原文而非 fallback（#655 L2 S2）", () => {
    const network = new Error("Network Error") as AxiosError;
    network.isAxiosError = true;
    expect(getErrorMessage(network, "请刷新重试")).toBe("Network Error");
  });
});

describe("ApiException", () => {
  it("是 Error 子类且携带 code/status", () => {
    const e = new ApiException("INVALID_AMOUNT", "金额非法", 422);
    expect(e).toBeInstanceOf(Error);
    expect(e.code).toBe("INVALID_AMOUNT");
    expect(e.status).toBe(422);
  });
});

describe("凭证续期与财务写入传输", () => {
  const originalAdapter = api.defaults.adapter;
  const user = { code: "ADMIN", name: "Admin", role: "admin" as const };
  const credential = () => ({ token: "fresh", expires_at: new Date(Date.now() + 7 * 86400000).toISOString(), user });

  beforeEach(() => {
    const values = new Map<string, string>([
      ["token", "old"],
      ["token-expires-at", new Date(Date.now() + 3600000).toISOString()],
    ]);
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    });
    vi.stubGlobal("window", { location: { pathname: "/dashboard", href: "" } });
    vi.spyOn(useAuthStore, "getState").mockReturnValue({
      ...useAuthStore.getState(), user,
      renew: (expected, data) => {
        if (localStorage.getItem("token") !== expected) return false;
        localStorage.setItem("token", data.token);
        localStorage.setItem("token-expires-at", data.expires_at);
        return true;
      },
      logout: () => localStorage.removeItem("token"),
    });
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function response(config: InternalAxiosRequestConfig, data: unknown, status = 200) {
    return { config, data, status, statusText: String(status), headers: {} };
  }

  function fail(config: InternalAxiosRequestConfig, status: number | "network"): never {
    throw new AxiosError("传输失败", undefined, config, undefined,
      status === "network" ? undefined : response(config, { detail: "传输失败" }, status));
  }

  it.each([200, 401, 503, "network"] as const)("续期在先，写入结果 %s 均只发送一次", async (status) => {
    const calls: string[] = [];
    api.defaults.adapter = async (config) => {
      calls.push(`${config.method} ${config.url}`);
      if (config.url === "/auth/renew") return response(config, credential());
      expect(config.headers.Authorization).toBe("Bearer fresh");
      if (status !== 200) fail(config, status);
      return response(config, { id: 42 });
    };
    const pending = request({ method: "POST", url: "/trades", data: { amount: 100 } });
    if (status === 200) await expect(pending).resolves.toEqual({ id: 42 });
    else await expect(pending).rejects.toBeInstanceOf(ApiException);
    expect(calls).toEqual(["post /auth/renew", "post /trades"]);
  });

  it.each([503, "network"] as const)("续期 %s 后用旧凭证，写入断网不重放", async (status) => {
    const calls: string[] = [];
    api.defaults.adapter = async (config) => {
      calls.push(config.url!);
      if (config.url === "/auth/renew") fail(config, status);
      expect(config.headers.Authorization).toBe("Bearer old");
      fail(config, "network");
    };
    await expect(request({ method: "POST", url: "/trades/42/confirm" })).rejects.toBeInstanceOf(ApiException);
    expect(calls).toEqual(["/auth/renew", "/trades/42/confirm"]);
    expect(localStorage.getItem("token")).toBe("old");
  });

  it("续期 401 在财务请求发送前终止", async () => {
    const calls: string[] = [];
    api.defaults.adapter = async (config) => {
      calls.push(config.url!);
      fail(config, 401);
    };
    await expect(request({ method: "POST", url: "/trades" })).rejects.toMatchObject({ status: 401 });
    expect(calls).toEqual(["/auth/renew"]);
    expect(localStorage.getItem("token")).toBeNull();
  });

  it.each(["missing", "expired", "other-owner"])("拒绝 %s 续期响应且不写入", async (kind) => {
    const calls: string[] = [];
    const data = credential();
    if (kind === "expired") data.expires_at = new Date(Date.now() - 1000).toISOString();
    if (kind === "other-owner") data.user = { ...user, code: "OTHER" };
    api.defaults.adapter = async (config) => {
      calls.push(config.url!);
      return response(config, kind === "missing" ? {} : data);
    };
    await expect(request({ method: "POST", url: "/trades" })).rejects.toMatchObject({ message: "续期响应无效，原凭证未替换" });
    expect(calls).toEqual(["/auth/renew"]);
    expect(localStorage.getItem("token")).toBe("old");
  });
});
