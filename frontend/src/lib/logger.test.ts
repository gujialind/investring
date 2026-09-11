/**
 * logger 单测（issue #407）
 *
 * 落在 `frontend/AGENTS.md` §3 的 lib 纯逻辑范围内：node 环境、不引 jsdom/RTL。
 * 覆盖验收断言：四级均输出、生产剔除 debug 而 error 仍输出、tag 前缀、
 * meta 透传（含 Error 不丢堆栈）、不可序列化值不抛错。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createLogger, logger } from "./logger";

const DEBUG_LINE = "[InvestRing][app][DEBUG] hello";
const INFO_LINE = "[InvestRing][app][INFO] hello";
const WARN_LINE = "[InvestRing][app][WARN] hello";
const ERROR_LINE = "[InvestRing][app][ERROR] hello";

describe("logger 分级输出", () => {
  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "development");
    vi.spyOn(console, "debug").mockImplementation(() => {});
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("开发环境下四级均输出到对应的 console 方法", () => {
    logger.debug("hello");
    logger.info("hello");
    logger.warn("hello");
    logger.error("hello");

    expect(console.debug).toHaveBeenCalledWith(DEBUG_LINE);
    expect(console.info).toHaveBeenCalledWith(INFO_LINE);
    expect(console.warn).toHaveBeenCalledWith(WARN_LINE);
    expect(console.error).toHaveBeenCalledWith(ERROR_LINE);
  });

  it("每级只打自己的 console 方法，不串级", () => {
    logger.warn("hello");

    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(console.info).not.toHaveBeenCalled();
    expect(console.error).not.toHaveBeenCalled();
    expect(console.debug).not.toHaveBeenCalled();
  });
});

describe("logger 生产环境剔除 debug", () => {
  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "production");
    vi.spyOn(console, "debug").mockImplementation(() => {});
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("生产构建下 debug 零输出", () => {
    logger.debug("hello");
    expect(console.debug).not.toHaveBeenCalled();
  });

  it("生产构建下 info / warn / error 仍输出", () => {
    logger.info("hello");
    logger.warn("hello");
    logger.error("hello");

    expect(console.info).toHaveBeenCalledWith(INFO_LINE);
    expect(console.warn).toHaveBeenCalledWith(WARN_LINE);
    expect(console.error).toHaveBeenCalledWith(ERROR_LINE);
  });

  it("判定发生在调用时，环境切换即时生效", () => {
    logger.debug("hello");
    expect(console.debug).not.toHaveBeenCalled();

    vi.stubEnv("NODE_ENV", "development");
    logger.debug("hello");
    expect(console.debug).toHaveBeenCalledWith(DEBUG_LINE);
  });
});

describe("logger 模块 tag", () => {
  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "development");
    vi.spyOn(console, "info").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("createLogger 的 tag 进统一前缀", () => {
    const log = createLogger("tradePairs");
    log.info("结对数不匹配");

    expect(console.info).toHaveBeenCalledWith("[InvestRing][tradePairs][INFO] 结对数不匹配");
  });

  it("不给 tag 时回落默认 tag", () => {
    createLogger().info("hello");
    expect(console.info).toHaveBeenCalledWith(INFO_LINE);
  });

  it("默认 logger 的 tag 为 app", () => {
    logger.info("hello");
    expect(console.info).toHaveBeenCalledWith(INFO_LINE);
  });

  it("同名 tag 的实例输出一致（同一模块复用不漂移）", () => {
    createLogger("snapshot").info("a");
    createLogger("snapshot").info("b");

    expect(console.info).toHaveBeenNthCalledWith(1, "[InvestRing][snapshot][INFO] a");
    expect(console.info).toHaveBeenNthCalledWith(2, "[InvestRing][snapshot][INFO] b");
  });
});

describe("logger meta 透传", () => {
  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "development");
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("无 meta 时不留尾随空格", () => {
    logger.error("炸了");
    expect(console.error).toHaveBeenCalledWith("[InvestRing][app][ERROR] 炸了");
  });

  it("对象 meta 序列化为 JSON", () => {
    logger.error("炸了", { portfolio: "E2E_PORT", status: 500 });

    expect(console.error).toHaveBeenCalledWith(
      '[InvestRing][app][ERROR] 炸了 {"portfolio":"E2E_PORT","status":500}'
    );
  });

  it("字符串 meta 原样拼接", () => {
    logger.error("炸了", "MISSING_NAV");
    expect(console.error).toHaveBeenCalledWith("[InvestRing][app][ERROR] 炸了 MISSING_NAV");
  });

  it("Error 的 message 与 stack 都保留（JSON.stringify 会丢堆栈）", () => {
    const err = new Error("boom");
    logger.error("渲染期异常", err);

    const line = vi.mocked(console.error).mock.calls[0][0] as string;
    expect(line.startsWith("[InvestRing][app][ERROR] 渲染期异常 boom")).toBe(true);
    expect(line).toContain("\n");
    expect(line).toContain("Error: boom");
  });

  it("循环引用不抛错，退化为 String()", () => {
    const circular: Record<string, unknown> = { name: "loop" };
    circular.self = circular;

    expect(() => logger.error("炸了", circular)).not.toThrow();
    const line = vi.mocked(console.error).mock.calls[0][0] as string;
    expect(line).toContain("[InvestRing][app][ERROR] 炸了");
  });

  it("数字/布尔/数组 meta 可序列化", () => {
    logger.error("a", 1);
    logger.error("b", false);
    logger.error("c", ["x", "y"]);

    expect(console.error).toHaveBeenNthCalledWith(1, "[InvestRing][app][ERROR] a 1");
    expect(console.error).toHaveBeenNthCalledWith(2, "[InvestRing][app][ERROR] b false");
    expect(console.error).toHaveBeenNthCalledWith(3, '[InvestRing][app][ERROR] c ["x","y"]');
  });
});
