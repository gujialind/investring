# ============================================================================
# InvestRing 前端 Dockerfile
# ============================================================================
# 基于 Node.js 多阶段构建，最终以 Next.js standalone 模式运行
# 上下文：frontend 目录（docker build -f frontend/Dockerfile frontend/）
# 注：根目录 .dockerignore 为后端镜像排除了 frontend/，故不能用根目录作上下文
# ============================================================================

# ------------------------------------------------------------------
# 阶段 1：安装依赖
# ------------------------------------------------------------------
FROM node:24-alpine AS deps

# npm 镜像源（可选）：国内环境构建可传 --build-arg NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /app

# 先复制包文件，利用 Docker 层缓存
COPY package.json package-lock.json ./

# 使用 ci 确保依赖与锁文件完全一致（CI/CD 推荐；默认含 devDependencies，构建需要）
RUN npm ci --registry=$NPM_REGISTRY

# ------------------------------------------------------------------
# 阶段 2：构建 Next.js 应用
# ------------------------------------------------------------------
FROM node:24-alpine AS builder

WORKDIR /app

# 复制依赖和源码
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# 构建时注入环境变量
# API_BASE_URL：Next.js rewrites 在 build 时固化，必须构建期注入
# 魔搭 Studio 不支持自定义 --build-arg，故默认值即为生产后端地址（走域名，不暴露服务器 IP）
ARG API_BASE_URL=https://investring.top
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production \
    API_BASE_URL=$API_BASE_URL

# Next.js 构建（生成 .next/ 目录）
RUN npm run build

# ------------------------------------------------------------------
# 阶段 3：生产运行层（最小化镜像）
# ------------------------------------------------------------------
FROM node:24-alpine AS runtime

WORKDIR /app

# 构建身份（#537 第四批）：可信 CI 以 --build-arg GIT_REVISION=<完整SHA> 注入，
# 写入 OCI revision 标签；scripts/smoke_images.py 对最终镜像校验标签与目标提交一致
ARG GIT_REVISION=
LABEL org.opencontainers.image.revision="${GIT_REVISION}"

# 安全：创建非 root 用户（魔搭平台用 su 切换用户，必须指定有效 shell）
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 --shell /bin/sh nextjs

# Standalone 模式（next.config.js 中 output: 'standalone'）：
# 只拷贝运行所需的最小产物，无需完整 node_modules
# 注意：standalone 不包含 public/ 和 .next/static/，需单独拷贝
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

# 环境变量（魔搭 Studio 强制要求端口 7860、监听 0.0.0.0）
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME="0.0.0.0" \
    PORT=7860

# 切换到非 root 用户
USER nextjs

# 暴露 Next.js 端口（魔搭 Studio 要求 7860）
EXPOSE 7860

# 健康检查（用 127.0.0.1：alpine 下 localhost 解析到 IPv6，而 Next 仅监听 IPv4）
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:7860/login || exit 1

# 启动 standalone 服务器（next build 生成的自包含 server.js）
CMD ["node", "server.js"]
