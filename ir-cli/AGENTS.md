# ir-cli/AGENTS.md — CLI 模块指南

> `ir` 是独立轻量 HTTP 客户端（typer + httpx），入口 `ir_cli.main:app`，经 HTTP 调用运行中的后端。先用 `ir schema --index` / `ir <命令组> --help` 定位，再读[使用手册](CLI_MANUAL.md)的对应命令组；从 `ir_cli/main.py` 的注册找到实现与测试，不假设模块名等于命令名（例如 `trade` 对应 [trades.py](ir_cli/commands/trades.py) 与 [现金参数测试](tests/test_trade_cash_params.py)），不默认加载完整手册。业务语义链接[业务规则](../docs/reference/business-constraints.md)对应领域，不在 CLI 另写一份；文档维护遵循 [AI 文档规范](../docs/reference/documentation.md)。

## 1. 跑测试

```bash
pip install -e ir-cli pytest && pytest ir-cli/tests -q   # 仓库根目录执行
```

## 2. 响应字段契约（CI 强制）

后端 API 响应结构变化后必须重新生成契约并**同一次提交**：

```bash
python ir-cli/scripts/gen_response_fields.py    # backend/openapi.json → ir_cli/response_fields.py
python ir-cli/scripts/gen_response_fields.py --check   # CI 用：不一致 exit 1
```

CI 的 `cli-contract-check` job 还校验 `backend/openapi.json` 本身无漂移（`backend/check_openapi.py`）。纯 stdlib 脚本、任意 cwd 可跑。

## 3. 契约语义

`ir schema` 输出含响应字段契约（`commands.<group>.<sub>.output.fields`，`*` 前缀=默认摘要字段、`?` 后缀=可空）与 `--index` 极简索引模式；改命令输出字段时同步更新 `utils.py` 的 SUMMARY_FIELDS 再重新生成。

## 4. 版本

`pyproject.toml` 的 version（`ir --version` 经 `importlib.metadata` 读取）由发布流程从仓库根 `VERSION` 同步，勿手改（见 `docs/reference/versioning.md`）。
