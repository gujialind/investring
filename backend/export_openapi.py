"""
导出 OpenAPI 规范 JSON 文件，用于导入 Apifox 等工具。

推荐离线生成当前源码契约（无需运行后端，不继承业务配置）：
  python export_openapi.py --offline
  python export_openapi.py --offline --output /path/to/openapi.json

兼容线上导出（输出路径默认相对当前工作目录）：
  python export_openapi.py [URL] [输出路径]
"""
import argparse
from pathlib import Path
import sys

from openapi_runtime import OPENAPI_PATH, generate_schema, validate_schema, write_schema

DEFAULT_URL = "http://localhost:8000/openapi.json"
OUTPUT_FILE = "openapi.json"

def fetch_online_schema(url: str) -> dict:
    # 离线入口只依赖标准库，不加载 requests 或调用方的线上连接配置。
    import requests

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return validate_schema(response.json())
    except (requests.RequestException, ValueError) as exc:
        # requests 异常可能含 URL 的认证信息，不回显异常原文。
        raise RuntimeError(f"线上 OpenAPI 获取失败（{type(exc).__name__}），可改用 --offline") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="隔离生成当前源码契约")
    parser.add_argument("--output", "-o", type=Path, help="输出文件路径")
    parser.add_argument("url", nargs="?", help="线上 OpenAPI 地址")
    parser.add_argument("output_path", nargs="?", type=Path, help="兼容线上导出的输出路径")
    args = parser.parse_args(argv)
    if args.offline and args.url:
        parser.error("--offline 不接受线上 URL；指定文件请使用 --output")
    if args.output and args.output_path:
        parser.error("--output 与位置参数输出路径不能同时使用")
    output = args.output or args.output_path or (OPENAPI_PATH if args.offline else Path(OUTPUT_FILE))

    print("正在隔离生成 OpenAPI 规范..." if args.offline else "正在获取线上 OpenAPI 规范...")
    try:
        spec = generate_schema() if args.offline else fetch_online_schema(args.url or DEFAULT_URL)
        write_schema(spec, output)
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"[error] OpenAPI 导出失败：{exc}", file=sys.stderr)
        return 1

    paths_count = len(spec["paths"])
    print(f"成功导出 {paths_count} 个接口路径到 {output}")
    print(f"\n导入 Apifox 步骤：")
    print(f"  1. 打开 Apifox → 项目设置 → 导入数据")
    print(f"  2. 选择 'OpenAPI/Swagger' 格式")
    print(f"  3. 上传 {output} 文件")
    print(f"  4. 选择导入模式（普通导入/自动合并）")
    print(f"  5. 确认导入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
