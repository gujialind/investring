# ============================================================================
# backend-test-mysql job 的连接身份守门（issue #539 第二单元）
# ============================================================================
# 该 job 的 pytest 会话开头就是一次 `drop_all`。连接身份一旦退回 root，「测试 URL
# 被指错 = 整个实例可毁」这条风险面就重新出现，而**没有任何用例会因此变红**——root
# 一样能通过 `tests/db_isolation.py` 的归属闸门（它判的是「这个库归不归 pytest」，
# 不是「这个账号有多小」）。所以第二层防线只能在 workflow 文本层面钉死：
# 每个 MySQL 连接串都用与库同名的专属账号，且该账号只被授予自己那个库的权限。
#
# 与 test_ci_path_mapping.py 同族的做法：不解析 YAML 语义，只按缩进切出 job 块做结构
# 断言，结构漂移（job 改名 / GRANT 缩进变化）时响亮失败而不是静默放行。判定逻辑收在
# 模块级函数里，好让下面的反例用例能对「root 被塞回来」「授权被削减」的合成文本跑同一套
# 判据——否则这些断言一旦因 workflow 重构而空跑，本身就成了一个不会红的门禁。
# ============================================================================

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

JOB = "backend-test-mysql"
#: pytest 会话会 drop_all/create_all，迁移步骤会 upgrade/downgrade 到 head，逐条对应：
#:   SELECT/INSERT/UPDATE/DELETE —— 业务表读写与用例造数
#:   CREATE/DROP/ALTER/INDEX     —— 建删表与索引；ALTER 另覆盖 0015 的 ALTER DATABASE
#:   REFERENCES                  —— 建外键约束需要父表上的 REFERENCES
REQUIRED_PRIVILEGES = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE",
    "CREATE", "DROP", "ALTER", "INDEX", "REFERENCES",
})
#: 出现即失去「最小权限」含义的授权形态（`*.*` 是全局授权，其余是管理/文件/转授权限）。
FORBIDDEN_GRANT_TOKENS = ("ALL PRIVILEGES", "GRANT OPTION", "SUPER", "FILE", "PROCESS")

URL_RE = re.compile(
    r"mysql\+\w+://(?P<user>[^:/@\s]+):(?P<password>[^@/\s]*)@[^/\s]+/(?P<db>[\w-]+)"
)
GRANT_RE = re.compile(r"(?m)^[ \t]*GRANT\s+(.*?)\s+ON\s+(?P<db>\w+)\.\*", re.S)
MYSQL_URL_LINES = re.compile(r"(?m)^.*mysql\+\w+://.*$")


def job_block(text: str) -> str:
    """切出 `backend-test-mysql` job 的原文（到下一个同级 job 为止），并去掉注释行。

    去注释不是美化：说明性注释里会出现 `GRANT OPTION`、`*.*` 这些**正是要禁止**的字样，
    留着它们，禁止项检查会被自己的文档喂出假阳性。断言只关心真实 SQL 与配置文本。
    """
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l == f"  {JOB}:"), None)
    if start is None:
        pytest.fail(f"ci.yml 中找不到 `  {JOB}:`——job 改名或挪走了？请同步更新本守门")
    end = next(
        (j for j in range(start + 1, len(lines)) if re.match(r"^  [\w-]+:\s*$", lines[j])),
        len(lines),
    )
    return "\n".join(l for l in lines[start:end] if not l.lstrip().startswith("#"))


def connections(block: str) -> list[tuple[str, str]]:
    """该 job 里每个 MySQL 连接串的 (库名, 账号)。"""
    return [(m.group("db"), m.group("user")) for m in URL_RE.finditer(block)]


def granted_privileges(block: str) -> dict[str, frozenset[str]]:
    """库名 → 该库上 GRANT 的权限集合（多条 GRANT 取并集）。"""
    acc: dict[str, set[str]] = {}
    for m in GRANT_RE.finditer(block):
        words = {w.strip().upper() for w in m.group(1).replace("\n", " ").split(",")}
        acc.setdefault(m.group("db"), set()).update(words)
    return {db: frozenset(ws) for db, ws in acc.items()}


def privileged_grant_hits(block: str) -> list[str]:
    """返回块内出现的越权授权形态（全局授权或管理类权限）。"""
    hits = []
    if re.search(r"(?m)^[ \t]*GRANT[^;]*ON\s+\*\.\*", block):
        hits.append("*.*")
    for token in FORBIDDEN_GRANT_TOKENS:
        if re.search(rf"(?m)^[ \t]*GRANT[^;]*\b{token}\b[^;]*ON", block):
            hits.append(token)
    return hits


def root_connections(block: str) -> list[str]:
    """以 root 身份连库的连接串行。

    只认 SQLAlchemy 连接串（`mysql+driver://user:pw@host/db`），故建库建号那一步的
    `-uroot`、容器健康检查的 `mysqladmin ping -uroot` 天然不在判定范围内——它们本来就必须是 root。
    """
    return [
        line
        for line in MYSQL_URL_LINES.findall(block)
        if any(user == "root" for _, user in connections(line))
    ]


# ============================================================================
# 真身：CI 配置必须满足的约束
# ============================================================================


@pytest.fixture(scope="module")
def block() -> str:
    return job_block(CI_YML.read_text(encoding="utf-8"))


class TestConnectionIdentity:
    def test_urls_exist_at_all(self, block):
        """一个 MySQL 连接串都没有 = 本守门空跑（job 被拆走也要在这里报红）。"""
        assert connections(block), f"{JOB} 里找不到 MySQL 连接串，守门无从判定"

    def test_no_root_connection(self, block):
        assert not root_connections(block), (
            "MySQL job 用 root 连库——测试会话的 drop_all 必须打在最小权限账号上（#539）："
            + "\n".join(root_connections(block))
        )

    def test_account_is_named_after_its_database(self, block):
        """库与账号一一对应，跨库就够不着：pytest 指错 URL 也碰不到迁移库。"""
        for db, user in connections(block):
            assert user == db, f"库 {db} 的连接账号是 {user}，与库同名约定不符"


class TestGrants:
    def test_every_used_account_is_created(self, block):
        for db, user in connections(block):
            assert re.search(rf"CREATE USER '{re.escape(user)}'@", block), (
                f"账号 {user} 没有 CREATE USER 语句——job 会在第一个连接上失败"
            )

    def test_every_used_account_is_granted_on_its_own_db(self, block):
        """授权必须「库 ↔ 账号」配对：只授权不给对的人，等于没建这道墙。"""
        for db, user in connections(block):
            assert re.search(
                rf"ON\s+{re.escape(db)}\.\*\s+TO\s+'{re.escape(user)}'@", block, re.S
            ), f"账号 {user} 未被授予自己库 {db} 的权限，drop_all/create_all 会失败"

    def test_privileges_cover_the_real_usage(self, block):
        grants = granted_privileges(block)
        assert grants, "job 块里一条 GRANT 语句都没解析到（缩进结构变了？请同步本守门）"
        for db, granted in grants.items():
            missing = REQUIRED_PRIVILEGES - granted
            assert not missing, (
                f"库 {db} 的授权少了 {sorted(missing)}：pytest 的 drop_all/create_all、"
                "外键建立与 0015 的库级/表级转码都会失败"
            )

    def test_no_privileged_grant(self, block):
        assert not privileged_grant_hits(block), (
            f"出现越权授权形态 {privileged_grant_hits(block)}，测试账号不再是最小权限"
        )


# ============================================================================
# 反例：守门本身必须会红
# ============================================================================


class TestGuardIsDiscriminating:
    """把违规形态塞进合成文本，判据必须逐条认出——否则上面那几条只是不会红的空跑。"""

    GOOD = """            GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES
              ON ir_test.* TO 'ir_test'@'%';"""

    def test_root_connection_is_caught(self):
        bad = self.GOOD + "\n          TEST_DB_URL: mysql+pymysql://root:root@127.0.0.1:3306/ir_test"
        assert root_connections(bad), "root 连接串未被认出——守门形同虚设"

    def test_cross_db_account_is_caught(self):
        bad = self.GOOD + "\n          TEST_DB_URL: mysql+pymysql://ir_migration:pw@127.0.0.1:3306/ir_test"
        assert ("ir_test", "ir_migration") in connections(bad)

    def test_reduced_privileges_are_caught(self):
        bad = """            GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX
              ON ir_test.* TO 'ir_test'@'%';"""
        assert "REFERENCES" in REQUIRED_PRIVILEGES - granted_privileges(bad)["ir_test"], (
            "漏掉 REFERENCES（建外键必需）没被认出——真实 CI 会红而守门不会"
        )

    def test_global_grant_is_caught(self):
        bad = "            GRANT SELECT ON *.* TO 'ir_test'@'%';"
        assert "*.*" in privileged_grant_hits(bad)

    def test_admin_privilege_is_caught(self):
        bad = "            GRANT SELECT, SUPER ON ir_test.* TO 'ir_test'@'%';"
        assert "SUPER" in privileged_grant_hits(bad)

    def test_prose_about_forbidden_grants_does_not_fake_a_hit(self):
        """文档注释里谈 `GRANT OPTION` / `*.*` 不算违规——去注释正是为这一点。"""
        text = (
            f"  {JOB}:\n"
            "      # 刻意不给：ALL PRIVILEGES / GRANT OPTION / 任何 *.* 授权\n"
            f"{self.GOOD}\n"
            "  next-job:\n"
        )
        stripped = job_block(text)
        assert not privileged_grant_hits(stripped), stripped
        assert "GRANT OPTION" not in stripped
