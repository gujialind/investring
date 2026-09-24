# ============================================================================
# InvestRing 后端 Dockerfile
# ============================================================================
# 基于 Python 3.12-slim，多阶段构建优化镜像大小
# 上下文：项目根目录（docker build -f backend/Dockerfile .）
# ============================================================================

# ------------------------------------------------------------------
# 阶段 1：依赖安装层（利用 Docker 缓存加速构建）
# ------------------------------------------------------------------
FROM python:3.12-slim AS deps

WORKDIR /app

# 使用阿里云 Debian 镜像（国内加速）
RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖（pymysql 编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY backend/requirements.txt .

# 安装 Python 依赖（使用阿里云 PyPI 镜像，国内加速）。
# --no-binary jsonpath 豁免：akshare>=1.18.90 依赖 jsonpath，该包仅发布
# sdist（纯 Python，无需编译），与 --only-binary :all: 冲突，
# 由 docker-build-smoke 门禁发现（issue #170，#196 引入 akshare 后构建即断）
RUN pip install --no-cache-dir --prefix=/install --only-binary :all: --no-binary jsonpath \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    -r requirements.txt

# ------------------------------------------------------------------
# 阶段 2：生产运行层（最小化镜像体积）
# ------------------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

# 构建身份（#537 第四批）：可信 CI 以 --build-arg GIT_REVISION=<完整SHA> 注入，
# 写入 OCI revision 标签；scripts/smoke_images.py 对最终镜像校验标签与目标提交一致，
# 保证「冒烟/晋级的制品」与「CI 验证的代码」是同一身份。本地构建缺省为空标签。
ARG GIT_REVISION=
LABEL org.opencontainers.image.revision="${GIT_REVISION}"

# 使用阿里云 Debian 镜像（国内加速）
RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# 安装运行时必要的系统库（不含 gcc 等编译工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    # 创建非 root 用户运行应用（安全最佳实践）
    && groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# 从 deps 阶段复制已安装的 Python 包
COPY --from=deps /install /usr/local

# 复制后端应用代码
COPY backend/ .

# 项目版本单一来源（#375）：app.main._resolve_version 逐级向上查找读取
COPY VERSION ./

# 设置 Python 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1

# 创建日志目录；确保 entrypoint 脚本可执行
RUN mkdir -p /app/logs && chmod +x /app/docker-entrypoint.sh && chown -R appuser:appuser /app

# 切换到非 root 用户
USER appuser

# 暴露 FastAPI 端口
EXPOSE 8000

# 健康检查：每 30 秒检测一次后端服务是否存活
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令：经 docker-entrypoint.sh 先一次性执行 alembic upgrade head 再启动 uvicorn。
# 迁移在 worker 启动前完成，避免 --workers 2 下多 worker 并发迁移竞态（issue #104）；
# entrypoint 带命令分流，`docker run ... python -m alembic current` 等非启动命令原样透传。
# --host 0.0.0.0 允许外部访问
# --workers 2：多 worker 下 nav_sync 定时任务经 MySQL GET_LOCK 互斥，不会重复执行
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
