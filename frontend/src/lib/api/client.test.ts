import { describe, it, expect } from "vitest";
import type { AxiosError } from "axios";
import { ApiException, getErrorMessage, handleApiError } from "./client";

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
