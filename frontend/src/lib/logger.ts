/**
 * 前端日志基建（issue #407）
 *
 * 纯逻辑模块：**不依赖 DOM / React / window**，故可按 `frontend/AGENTS.md` §3
 * 落在 node 环境的 Vitest 单测范围内（组件交互与运行时行为归 Playwright E2E）。
 *
 * 四级 `debug` / `info` / `warn` / `error` + 模块 tag，输出带统一前缀
 * （`[InvestRing][模块][LEVEL]`）。`debug` 在生产构建下直接 no-op——
 * 判定用 `process.env.NODE_ENV === "production"`，Next 在构建期做静态替换，
 * 既能整段摇掉、也无需引入构建插件（§3.5 铁律 3：不加未要求的依赖）。
 *
 * 本文件是**全仓唯一**允许直调 `console.*` 的地方：`eslint.config.mjs` 在
 * `src/**` 上禁裸 console，并在此文件定点豁免（豁免登记见
 * `docs/design/visual-spec.md` §1.5 与 `docs/reference/logging.md`）。
 *
 * 刻意不做客户端错误上报（总纲 #403 决策 3）：避免改动 openapi 契约与引入
 * 上报端点的滥用/限流问题。要上报请另开 issue。
 */

/** 日志级别，级别递进（数值越大越严重） */
export type LogLevel = "debug" | "info" | "warn" | "error";

/** 统一前缀，便于在浏览器控制台按关键字过滤 */
const PREFIX = "InvestRing";

/** 全局 root tag（不带模块 tag 的默认 logger 使用） */
const DEFAULT_TAG = "app";

/** console 方法映射：逐级对应，不做降级（warn 打到 console.warn 才拿得到调用栈标记） */
const CONSOLE_METHOD: Record<LogLevel, "debug" | "info" | "warn" | "error"> = {
  debug: "debug",
  info: "info",
  warn: "warn",
  error: "error",
};

export interface Logger {
  debug(message: string, meta?: unknown): void;
  info(message: string, meta?: unknown): void;
  warn(message: string, meta?: unknown): void;
  error(message: string, meta?: unknown): void;
}

function isProduction(): boolean {
  return process.env.NODE_ENV === "production";
}

function formatPrefix(tag: string, level: LogLevel): string {
  return `[${PREFIX}][${tag}][${level.toUpperCase()}]`;
}

/**
 * 序列化非字符串的 meta。
 *
 * `Error` 单独处理：`JSON.stringify(new Error("x"))` 得到 `{}`（message/stack 不可枚举），
 * 原样序列化等于把堆栈丢掉——错误留痕正是本模块的主要用途。
 */
function formatMeta(meta: unknown): string {
  if (meta === undefined) return "";
  if (typeof meta === "string") return ` ${meta}`;
  if (meta instanceof Error) {
    const stack = meta.stack ? `\n${meta.stack}` : "";
    return ` ${meta.message}${stack}`;
  }
  try {
    return ` ${JSON.stringify(meta)}`;
  } catch {
    // 循环引用 / BigInt 等不可序列化值：退化为 String()，绝不因日志本身抛错
    return ` ${String(meta)}`;
  }
}

function emit(tag: string, level: LogLevel, message: string, meta?: unknown): void {
  // 生产构建剔除 debug：判定放在调用时，单测可 stub env 覆盖（模块级常量会锁死取值）
  if (level === "debug" && isProduction()) return;
  // 本文件是 console.* 的唯一封装层，eslint.config.mjs 对 src/lib/logger.ts 定点豁免
  console[CONSOLE_METHOD[level]](`${formatPrefix(tag, level)} ${message}${formatMeta(meta)}`);
}

/** 带模块 tag 的 logger（同一模块复用同一实例，输出前缀一致） */
export function createLogger(tag = DEFAULT_TAG): Logger {
  return {
    debug: (message, meta) => emit(tag, "debug", message, meta),
    info: (message, meta) => emit(tag, "info", message, meta),
    warn: (message, meta) => emit(tag, "warn", message, meta),
    error: (message, meta) => emit(tag, "error", message, meta),
  };
}

/** 无模块 tag 的默认 logger（tag 落 `app`） */
export const logger: Logger = createLogger();
