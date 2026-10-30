"""
配置与 token 管理

管理 ~/.ir/ 目录，读写 token.json、config 和本地缓存。
"""
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def get_ir_dir() -> Path:
    """返回 ~/.ir/ 目录（权限 0700），不存在则创建"""
    ir_dir = Path.home() / ".ir"
    ir_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return ir_dir


def get_base_url() -> str:
    """
    获取服务端 base_url。
    优先级: IR_BASE_URL 环境变量 > ~/.ir/config > 默认 localhost:8000
    """
    env_url = os.environ.get("IR_BASE_URL")
    if env_url:
        return env_url.rstrip("/")

    config_file = get_ir_dir() / "config"
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("base_url="):
                return line.split("=", 1)[1].strip().rstrip("/")

    return "http://localhost:8000"


@contextmanager
def token_lock():
    """稳定锁文件保护原子替换；锁文件不能随 token 删除。"""
    fd = os.open(get_ir_dir() / "token.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.write(b"\0")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def save_token(data: dict, *, expected_token: Optional[str] = None) -> bool:
    """锁内比较再替换，防续期覆盖并发登出/新登录；临时文件从创建即为 0600。"""
    with token_lock():
        if expected_token is not None:
            current = load_token(include_expired=True)
            if not current or current["token"] != expected_token:
                return False
        token_file = get_ir_dir() / "token.json"
        fd, temporary = tempfile.mkstemp(prefix=".token-", dir=token_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, token_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return True


def token_remaining(data: dict) -> Optional[float]:
    expires_at = data.get("expires_at")
    if not expires_at:
        return None
    exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (exp - datetime.now(timezone.utc)).total_seconds()


def load_token(*, include_expired: bool = False) -> Optional[dict]:
    """仅读本地文件，无网络/诊断副作用。"""
    try:
        data = json.loads((get_ir_dir() / "token.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("token"), str) or not data["token"]:
            return None
        remaining = token_remaining(data)
        if not include_expired and remaining is not None and remaining <= 0:
            return None
        return data
    except (ValueError, TypeError, AttributeError, OSError):
        return None


def clear_token(*, expected_token: Optional[str] = None) -> None:
    """仅清除调用方使用的凭证，不误删并发新登录。"""
    with token_lock():
        if expected_token is not None:
            current = load_token(include_expired=True)
            if not current or current["token"] != expected_token:
                return
        (get_ir_dir() / "token.json").unlink(missing_ok=True)


def load_config() -> dict:
    """读取 ~/.ir/config 全部配置项（key=value 格式）"""
    config_file = get_ir_dir() / "config"
    result = {}
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def save_config(key: str, value: str) -> None:
    """写入配置项到 ~/.ir/config"""
    config_file = get_ir_dir() / "config"
    lines = []
    found = False
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 本地缓存（目前用于交易日历） ----------

def _cache_dir() -> Path:
    cache_dir = get_ir_dir() / "cache"
    cache_dir.mkdir(mode=0o700, exist_ok=True)
    return cache_dir


def load_cache(name: str, ttl_seconds: int) -> Optional[Any]:
    """读取本地缓存，超过 TTL 或损坏时返回 None"""
    cache_file = _cache_dir() / f"{name}.json"
    if not cache_file.exists():
        return None
    try:
        wrapper = json.loads(cache_file.read_text(encoding="utf-8"))
        if time.time() - wrapper["cached_at"] > ttl_seconds:
            return None
        return wrapper["data"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, OSError):
        return None


def save_cache(name: str, data: Any) -> None:
    """写入本地缓存（失败静默忽略，缓存不影响主流程）"""
    try:
        cache_file = _cache_dir() / f"{name}.json"
        cache_file.write_text(json.dumps({"cached_at": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def clear_cache(prefix: str = "") -> int:
    """清除缓存文件（可按前缀），返回删除数量"""
    count = 0
    for f in _cache_dir().glob(f"{prefix}*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
