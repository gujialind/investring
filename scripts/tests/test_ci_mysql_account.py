# ============================================================================
# CI MySQL 连接身份守门：两个目标、一套判据（#539 第二单元 → #548 泛化）
# ============================================================================
# 覆盖面 = TARGETS 里每一项 (workflow 文件, job, 期望连接串条数)。当前两个目标：
#   - ci.yml:backend-test-mysql —— pytest 会话开头就是一次 drop_all，迁移步骤还做
#     upgrade head → downgrade -1 → upgrade head，权限面最广。
#   - e2e-stack.yml:e2e —— 三条连接串全指 ir_e2e，跑 create_all + alembic upgrade head
#     两遍（迁移步骤一遍；Start backend 时 main.py:30 的 import 期 create_all 与 :47 的
#     lifespan upgrade head 再各一遍）。**没有 drop_all、也没有 downgrade。**
#
# 连接身份一旦退回 root，「URL 被指错 = 这个账号够得着的东西就能被毁」这条风险面就重新
# 出现，而**没有任何用例会因此变红**——root 一样能通过 `tests/db_isolation.py` 的归属闸门
# （它判的是「这个库归不归 pytest」，不是「这个账号有多小」）；E2E 侧更是要从头到尾不看
# 归属闸门也不看权限大小，DBACL 就是那里唯一的结构防线。所以第二层防线只能在 workflow
# 文本层面钉死：每个 MySQL 连接串都用与库同名的专属账号，且该账号只被授予自己那个库的权限。
#
# 为什么两个目标共用一套判据、而不是各写一份守门（#548 决策）：判据抄成两份就必然漂移，
# 而 #539 写这道守门要防的正是静默漂移。新增一个 MySQL job 的成本 = TARGETS 加一行 + 把该
# job 的真实权限用法补进下面 REQUIRED_PRIVILEGES 的理由清单。
#
# 与 test_ci_path_mapping.py 同族的做法：不解析 YAML 语义，只按缩进切出 job 块做结构断言，
# 结构漂移（job 改名 / GRANT 缩进变化）时响亮失败而不是静默放行。判定逻辑收在模块级函数里，
# 好让下面的反例用例能对「root 被塞回来」「授权被削减」的合成文本跑同一套判据——否则这些断言
# 一旦因 workflow 重构而空跑，本身就成了一个不会红的门禁。反例对**每个目标**各跑一遍：只在
# 一个目标上成立的解析器，等于在另一个目标上空跑。
# ============================================================================

import itertools
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
E2E_STACK_YML = REPO_ROOT / ".github" / "workflows" / "e2e-stack.yml"

#: 两个 workflow 里 GRANT 的权限清单原文（与真身逐字一致，只差库名）。刻意不从上下的
#: REQUIRED_PRIVILEGES 派生：两者是独立事实，一致性由
#: TestCoverageIntegrity::test_counterexample_template_matches_required_privileges 钉。
GRANT_PRIVILEGES_TEXT = (
    "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES"
)


@dataclass(frozen=True)
class Target:
    """一个受守门的 MySQL job。frozen 且可哈希——直接作 fixture params 用。"""

    path: Path
    job: str
    #: 该 job 块内 MySQL 连接串的期望条数：静默增/删一条连接 = 有人加了连库步骤而没同步
    #: 账号口径（或把某个连库步骤拆走了），两种都该变红，而不是悄悄放行。
    expected_connections: int
    #: 反例用例的取材库名（同名账号约定下同时是账号名）与环境变量名。
    example_db: str
    example_env: str
    #: 跨库反例用的另一个库名：仓库 CI 里真实存在、但本 job 的账号够不着。
    foreign_db: str

    @property
    def label(self) -> str:
        """进 pytest id 与所有失败消息：结构漂移时一眼看出是哪个目标。"""
        return f"{self.path.name}:{self.job}"


TARGETS = [
    Target(
        path=CI_YML,
        job="backend-test-mysql",
        expected_connections=2,  # DATABASE_URL(迁移) + TEST_DB_URL(pytest)
        example_db="ir_test",
        example_env="TEST_DB_URL",
        foreign_db="ir_migration",
    ),
    Target(
        path=E2E_STACK_YML,
        job="e2e",
        expected_connections=3,  # 建 schema / 种子 / Start backend 各一条
        example_db="ir_e2e",
        example_env="DATABASE_URL",
        foreign_db="ir_test",
    ),
]

#: 两个目标真实用法的**并集**，逐条对应：
#:   SELECT/INSERT/UPDATE/DELETE —— 业务表读写、用例造数、E2E 种子与 init_scheduled_tasks
#:   CREATE/DROP/ALTER/INDEX     —— create_all/drop_all 建删表与索引（含 alembic_version、
#:                                   apscheduler_jobs 这类非模型表）；ALTER 另覆盖迁移 0015 的
#:                                   ALTER DATABASE（库级 ALTER 正是由 db.* 上的 ALTER 授予）
#:   REFERENCES                  —— 建外键需要**父表**上的该权限，漏了 create_all 当场失败
#: 只有 backend-test-mysql 跑 downgrade（0015/0016 的 downgrade 拆/重建外键，仍落在
#: DROP/CREATE/ALTER/REFERENCES 面内）；e2e 既不 drop_all 也不 downgrade，这份清单对它只是
#: 上界——共用一份、不分叉是 #548 的决策。充分性的证据是 main 上 Backend Tests (MySQL)
#: 以此清单为绿，而那恰是两目标里权限需求更宽的那个。收紧任何一项前，先在两个 job 上各红一次。
REQUIRED_PRIVILEGES = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE",
    "CREATE", "DROP", "ALTER", "INDEX", "REFERENCES",
})
#: 出现即失去「最小权限」含义的授权形态（`*.*` 是全局授权，其余是管理/文件/转授权限）。
FORBIDDEN_GRANT_TOKENS = ("ALL PRIVILEGES", "GRANT OPTION", "SUPER", "FILE", "PROCESS")

URL_RE = re.compile(
    r"mysql\+\w+://(?P<user>[^:/@\s]+):(?P<password>[^@/\s]*)@[^/\s]+/(?P<db>[\w-]+)"
)
# db 组与 URL_RE 同宽（[\w-]+ 而非 \w+）：否则带连字符的库名在 GRANT 侧解析不出来，而
# test_privileges_cover_the_real_usage 是**遍历解析到的 grants**——漏一个库 = 静默不检查。
GRANT_RE = re.compile(r"(?m)^[ \t]*GRANT\s+(.*?)\s+ON\s+(?P<db>[\w-]+)\.\*", re.S)
MYSQL_URL_LINES = re.compile(r"(?m)^.*mysql\+\w+://.*$")


def job_block(text: str, *, job: str, source: str) -> str:
    """切出 `job` 的原文（到下一个同级 job 为止；**没有下一个就到文件尾**），并去掉注释行。

    `source` 只服务于失败消息：真身调用传 `target.label`（含文件名），反例调用传合成标签——
    否则「job 改名」这条消息会指着错的文件。

    去注释不是美化：说明性注释里会出现 `GRANT OPTION`、`*.*` 这些**正是要禁止**的字样
    （e2e-stack.yml 新增的那段「刻意不给什么」就是），留着它们，禁止项检查会被自己的
    文档喂出假阳性。断言只关心真实 SQL 与配置文本。
    """
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l == f"  {job}:"), None)
    if start is None:
        pytest.fail(f"{source} 中找不到 `  {job}:`——job 改名或挪走了？请同步更新本守门")
    end = next(
        (j for j in range(start + 1, len(lines)) if re.match(r"^  [\w-]+:\s*$", lines[j])),
        len(lines),
    )
    return "\n".join(l for l in lines[start:end] if not l.lstrip().startswith("#"))


def connections(block: str) -> list[tuple[str, str]]:
    """该 job 里每个 MySQL 连接串的 (库名, 账号)。按出现顺序、**不去重**——条数本身是被断言的量。"""
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

    **只认 SQLAlchemy 连接串**（`mysql+driver://user:pw@host/db`）——这一点在两个目标上都是
    承重的：service 定义体本身就落在被断言的 job 块内（`e2e-stack.yml:71-82` ⊂ 66→EOF、
    `ci.yml:228-239` ⊂ 212→325），所以 `MYSQL_ROOT_PASSWORD: root`、健康检查的
    `mysqladmin ping ... -uroot -proot`、以及建库建号那一步的 `-uroot` 全都在块内、
    但**必须不判**——它们本来就只能用 root。把这里改成「行内含 root 就算」会同时打红两个目标。
    """
    return [
        line
        for line in MYSQL_URL_LINES.findall(block)
        if any(user == "root" for _, user in connections(line))
    ]


def good_grant(target: Target) -> str:
    """合规 GRANT 文本，与两个 workflow 里的书写形态逐字同形（GRANT 行 12 空格、ON 续行 14）。"""
    return (
        f"            GRANT {GRANT_PRIVILEGES_TEXT}\n"
        f"              ON {target.example_db}.* TO '{target.example_db}'@'%';"
    )


def url_line(target: Target, user: str) -> str:
    """一条连接串的真实书写形态（env 缩进 10 空格）。"""
    return (
        f"          {target.example_env}: "
        f"mysql+pymysql://{user}:{user}@127.0.0.1:3306/{target.example_db}?charset=utf8mb4"
    )


# ============================================================================
# 真身：CI 配置必须满足的约束
#   target 参数化挂在 fixture 上，下面**所有** request 它的用例自动 × 目标数。
#   文件读取只在 block fixture 体内发生：若在 import 期读，job 改名会塌成一条
#   collection error（一个红格子、丢掉 per-target 粒度、且连带反例组一起不跑）。
# ============================================================================


@pytest.fixture(scope="module", params=TARGETS, ids=lambda t: t.label)
def target(request) -> Target:
    return request.param


@pytest.fixture(scope="module")
def block(target) -> str:
    return job_block(
        target.path.read_text(encoding="utf-8"), job=target.job, source=target.label
    )


class TestConnectionIdentity:
    def test_urls_exist_at_all(self, target, block):
        """一个 MySQL 连接串都没有 = 本守门空跑（job 被拆走也要在这里报红）。"""
        assert connections(block), f"{target.label} 里找不到 MySQL 连接串，守门无从判定"

    def test_connection_count_is_pinned(self, target, block):
        """条数钉死（#548）：多一条 = 有新连库步骤绕过账号口径；少一条 = 有步骤被拆走。"""
        got = connections(block)
        assert len(got) == target.expected_connections, (
            f"{target.label} 有 {len(got)} 条 MySQL 连接串 {got}，期望 "
            f"{target.expected_connections} 条——增删连库步骤时请同步本守门与 workflow"
        )

    def test_no_root_connection(self, target, block):
        bad = root_connections(block)
        assert not bad, (
            f"{target.label} 用 root 连库——最小权限账号正是这道防线的全部内容（#539/#548）：\n"
            + "\n".join(bad)
        )

    def test_account_is_named_after_its_database(self, target, block):
        """库与账号一一对应，跨库就够不着：连接串指错也碰不到别人的库。"""
        for db, user in connections(block):
            assert user == db, (
                f"{target.label}：库 {db} 的连接账号是 {user}，与库同名约定不符"
            )


class TestGrants:
    def test_every_used_account_is_created(self, target, block):
        for db, user in connections(block):
            assert re.search(rf"CREATE USER '{re.escape(user)}'@", block), (
                f"{target.label}：账号 {user} 没有 CREATE USER 语句——job 会在第一个连接上失败"
            )

    def test_every_used_account_is_granted_on_its_own_db(self, target, block):
        """授权必须「库 ↔ 账号」配对：只授权不给对的人，等于没建这道墙。"""
        for db, user in connections(block):
            assert re.search(
                rf"ON\s+{re.escape(db)}\.\*\s+TO\s+'{re.escape(user)}'@", block, re.S
            ), f"{target.label}：账号 {user} 未被授予自己库 {db} 的权限，drop_all/create_all 会失败"

    def test_privileges_cover_the_real_usage(self, target, block):
        grants = granted_privileges(block)
        assert grants, (
            f"{target.label} 的 job 块里一条 GRANT 都没解析到（缩进结构变了？请同步本守门）"
        )
        for db, granted in grants.items():
            missing = REQUIRED_PRIVILEGES - granted
            assert not missing, (
                f"{target.label}：库 {db} 的授权少了 {sorted(missing)}——建删表与索引、"
                "外键建立（REFERENCES）、迁移 0015 的 ALTER DATABASE 都会失败"
            )

    def test_no_privileged_grant(self, target, block):
        hits = privileged_grant_hits(block)
        assert not hits, f"{target.label}：出现越权授权形态 {hits}，测试账号不再是最小权限"


# ============================================================================
# 覆盖完整性：守门看不见的位置，比守门判定失败更坏（那是假绿灯）
# ============================================================================


def test_every_mysql_connection_in_covered_files_is_covered_by_a_target():
    """文件级连接串总数 == 该文件各目标块内的期望条数之和。

    job 块**只覆盖 `  <job>:` 到下一个同级键**，故 workflow 级 env（`e2e-stack.yml:60-63`，
    在 `jobs:` 之上）以及任何新增的、没进 TARGETS 的 MySQL job 都在判定面之外。没有这一条，
    「给 ci.yml 加第三个 MySQL job 并用 root」可以让本守门全绿。
    """
    for path, group in itertools.groupby(
        sorted(TARGETS, key=lambda t: str(t.path)), key=lambda t: t.path
    ):
        expected = sum(t.expected_connections for t in group)
        actual = len(URL_RE.findall(path.read_text(encoding="utf-8")))
        assert actual == expected, (
            f"{path.name} 全文有 {actual} 条 MySQL 连接串，TARGETS 只覆盖 {expected} 条"
            "——把新 job 加进 TARGETS，或让它指到该用的最小权限账号"
        )


class TestCoverageIntegrity:
    def test_counterexample_template_matches_required_privileges(self):
        """GRANT 原文清单与判据清单不分叉，且合规模板真能过判据。

        两者刻意都是字面量、互不派生：若模板由 REQUIRED_PRIVILEGES 生成，削减类反例就只是在
        跟自己的影子较劲，这个断言也会恒真。
        """
        assert frozenset(GRANT_PRIVILEGES_TEXT.replace(" ", "").split(",")) == (
            REQUIRED_PRIVILEGES
        ), "workflow 的 GRANT 清单与判据清单分叉了——两边必须一起改，或明确按 job 收紧"
        for t in TARGETS:
            assert not REQUIRED_PRIVILEGES - granted_privileges(good_grant(t))[t.example_db], (
                f"{t.label} 的合规模板本身过不了权限判据，反例组全部失真"
            )


# ============================================================================
# 反例：守门本身必须会红（对每个目标各跑一遍）
# ============================================================================


class TestGuardIsDiscriminating:
    """把违规形态塞进合成文本，判据必须逐条认出——否则上面那几条只是不会红的空跑。

    刻意只 request `target`、不 request `block`：真实 workflow 结构漂移时这组仍要能跑，
    否则「守门会不会红」这件事本身就跟着 workflow 一起挂了。
    """

    def test_root_connection_is_caught(self, target):
        good, bad = url_line(target, target.example_db), url_line(target, "root")
        assert root_connections(good) == [], "合规连接串被误报为 root——判定面不可信"
        assert root_connections(bad) == [bad], "root 连接串未被认出——守门形同虚设"

    def test_cross_db_account_is_caught(self, target):
        line = (
            f"          {target.example_env}: "
            f"mysql+pymysql://{target.foreign_db}:{target.foreign_db}"
            f"@127.0.0.1:3306/{target.example_db}?charset=utf8mb4"
        )
        assert (target.example_db, target.foreign_db) in connections(line)
        assert not all(user == db for db, user in connections(line)), (
            "同名约定断言认不出跨库账号"
        )

    def test_missing_create_user_is_caught(self, target):
        block = f"{good_grant(target)}\n{url_line(target, target.example_db)}"
        assert connections(block) == [(target.example_db, target.example_db)]
        assert not re.search(
            rf"CREATE USER '{re.escape(target.example_db)}'@", block
        ), "缺 CREATE USER 的形态没被认出来——那条断言是空跑"

    def test_reduced_privileges_are_caught(self, target):
        reduced = GRANT_PRIVILEGES_TEXT.replace(", REFERENCES", "")
        bad = (
            f"            GRANT {reduced}\n"
            f"              ON {target.example_db}.* TO '{target.example_db}'@'%';"
        )
        assert "REFERENCES" in REQUIRED_PRIVILEGES - granted_privileges(bad)[
            target.example_db
        ], f"{target.label}：漏掉 REFERENCES（建外键必需）没被认出——真实 CI 会红而守门不会"

    def test_global_grant_is_caught(self, target):
        bad = f"            GRANT SELECT ON *.* TO '{target.example_db}'@'%';"
        assert "*.*" in privileged_grant_hits(bad)

    def test_admin_privilege_is_caught(self, target):
        bad = (
            f"            GRANT SELECT, SUPER ON {target.example_db}.* "
            f"TO '{target.example_db}'@'%';"
        )
        assert "SUPER" in privileged_grant_hits(bad)

    def test_prose_about_forbidden_grants_does_not_fake_a_hit(self, target):
        """文档注释里谈 `GRANT OPTION` / `*.*` 不算违规——去注释正是为这一点。

        与真身同形：e2e-stack.yml 新增的注释块逐条点了 ALL PRIVILEGES / GRANT OPTION /
        SUPER / FILE / PROCESS，全靠这里的剥离才不假阳性。合成块用 `  next-job:` 收尾
        （`[\\w-]+` 含连字符，故对 job 名 `e2e` 也能正确切）。
        """
        text = (
            f"  {target.job}:\n"
            "      # 刻意不给：ALL PRIVILEGES / GRANT OPTION / 任何 *.* 授权\n"
            f"{good_grant(target)}\n"
            "  next-job:\n"
        )
        stripped = job_block(
            text, job=target.job, source=f"{target.label}（反例合成块）"
        )
        assert not privileged_grant_hits(stripped), stripped
        assert "GRANT OPTION" not in stripped
