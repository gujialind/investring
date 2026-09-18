"""在临时目录和独立子进程内生成 OpenAPI，不继承业务运行环境（#539）。"""

import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

BACKEND_DIR = Path(__file__).resolve().parent
OPENAPI_PATH = BACKEND_DIR / "openapi.json"
TIMEOUT_SECONDS = 60

# -I 排除调用方的 Python 搜索路径；只显式加入当前仓库的后端目录。
_SCHEMA_CODE = """
import json
import sys
sys.path.insert(0, sys.argv[1])
from app.main import app
with open(sys.argv[2], "w", encoding="utf-8") as output:
    json.dump(app.openapi(), output, ensure_ascii=False, indent=2)
"""


def validate_schema(spec: object) -> dict:
    """拒绝错误页或非 OpenAPI JSON，避免覆盖已有契约。"""
    if not (
        isinstance(spec, dict)
        and isinstance(spec.get("openapi"), str)
        and isinstance(spec.get("info"), dict)
        and isinstance(spec.get("paths"), dict)
    ):
        raise ValueError("生成结果不是有效的 OpenAPI 对象（缺少 openapi/info/paths）")
    return spec


def _child_env(directory: Path) -> dict[str, str]:
    """仅继承启动解释器所需的系统变量，不透传应用、代理或 Python 配置。"""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update(
        HOME=str(directory),
        TMPDIR=str(directory),
        TMP=str(directory),
        TEMP=str(directory),
        LANG="C.UTF-8",
        DATABASE_URL=f"sqlite:///{directory / 'openapi.db'}",
        SCHEDULER_ENABLED="false",
        DEBUG="false",
        SECRET_KEY=secrets.token_hex(32),
    )
    return env


def _safe_diagnostics(stderr: str, env: dict[str, str]) -> str:
    """保留异常堆栈，同时隐藏临时密钥和可能由依赖输出的连接地址。"""
    diagnostic = stderr.replace(env["SECRET_KEY"], "[REDACTED]")
    return re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]+", "[REDACTED_URL]", diagnostic).strip()


def generate_schema() -> dict:
    """生成当前源码的 schema；失败、超时或资源清理失败均向调用方报错。"""
    try:
        with tempfile.TemporaryDirectory(prefix="investring-openapi-") as temporary:
            directory = Path(temporary)
            output = directory / "schema.json"
            env = _child_env(directory)
            # run 超时后会 kill 并 wait，退出后才清理目录（包含 SQLite WAL/SHM）。
            # 初始化日志不是 schema；失败诊断保留在脱敏后的 stderr 中。
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8", "-c", _SCHEMA_CODE,
                 str(BACKEND_DIR), str(output)],
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            diagnostic = _safe_diagnostics(result.stderr, env)
            if result.returncode:
                raise RuntimeError(
                    f"隔离 OpenAPI 生成失败（exit {result.returncode}）"
                    + (f"：\n{diagnostic}" if diagnostic else "，子进程未提供异常信息")
                )
            if diagnostic:
                print(diagnostic, file=sys.stderr)
            return validate_schema(json.loads(output.read_text(encoding="utf-8")))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"隔离 OpenAPI 生成超时（{TIMEOUT_SECONDS} 秒），子进程已终止") from None
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"OpenAPI 临时资源或生成结果处理失败（{type(exc).__name__}）") from None


def write_schema(spec: dict, output: Path) -> None:
    """先完成序列化，再原子替换目标文件；失败不破坏旧契约。"""
    content = json.dumps(validate_schema(spec), ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if output.exists():
            temporary.chmod(output.stat().st_mode & 0o777)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
