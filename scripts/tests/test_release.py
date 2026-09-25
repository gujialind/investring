import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_under_test", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
sys.path.insert(0, str(ROOT / "scripts"))
import release_bundle as rb  # noqa: E402  # alias 与发布包同源判据，直接复用真实模块

REPO = "example/investring"
PREFIX = f"repos/{REPO}"
TAG = "v0.1.0"


def version_files(version="0.1.0"):
    return {
        "VERSION": f"{version}\r\n",
        "backend/pyproject.toml": f'[project]\r\nversion = "{version}"\r\n',
        "ir-cli/pyproject.toml": f'[project]\nversion = "{version}"\n',
        "frontend/package.json": json.dumps({"name": "fixture", "version": version}, indent=2) + "\n",
        "frontend/package-lock.json": json.dumps({
            "version": version, "packages": {"": {"version": version}, "node_modules/dependency": {"version": "9.9.9"}},
        }, indent=2) + "\n",
        "backend/openapi.json": json.dumps({"info": {"version": version}, "paths": {}}) + "\n",
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root, remote = tmp_path / "repo", tmp_path / "origin.git"
    root.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "GITHUB_"))}
    env.update(
        HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Release fixture", GIT_AUTHOR_EMAIL="release@example.invalid",
        GIT_COMMITTER_NAME="Release fixture", GIT_COMMITTER_EMAIL="release@example.invalid",
    )
    real_run = subprocess.run

    def git(*args, data=None, cwd=root):
        result = real_run(["git", *map(str, args)], cwd=cwd, env=env, input=data, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    git("init", "-q", "--initial-branch=main", root)
    git("init", "-q", "--bare", remote)

    def commit(files, parent=None, *, publish=True):
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.unlink()
            else:
                path.write_bytes(content.encode())
        if files:
            git("add", "--", *files)
        tree = git("write-tree")
        sha = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", "fixture")
        git("update-ref", "refs/heads/main", sha)
        if publish:
            git("fetch", "--no-tags", root, "refs/heads/main:refs/heads/main", cwd=remote)
        return sha

    sha = commit(version_files())
    state = SimpleNamespace(
        root=root, remote=remote, git=git, commit=commit, sha=sha, calls=[], responses={},
        remote_url=f"https://user:private-token@github.com/{REPO}.git", failure=None,
        create_output=f"https://github.com/{REPO}/pull/123\n", docker=None,
    )

    def select(target):
        state.pr = {
            "number": 123, "state": "closed", "merged": True, "merged_at": "2026-09-24T00:00:00Z",
            "title": f"chore(release): {TAG}", "merge_commit_sha": target,
            "base": {"ref": "main", "repo": {"full_name": REPO}},
            "head": {"ref": f"release/{TAG}", "repo": {"full_name": REPO}},
        }
        state.ci = {
            "id": 100, "workflow_id": 7, "path": ".github/workflows/ci.yml", "name": "CI",
            "head_sha": target, "head_branch": "main", "event": "push",
            "status": "completed", "conclusion": "success", "head_repository": {"full_name": REPO},
        }
        state.responses.update({
            f"{PREFIX}/pulls/123": state.pr,
            f"{PREFIX}/commits/{target}/pulls": [[state.pr]],
            f"{PREFIX}/actions/workflows/ci.yml": {"id": 7, "path": ".github/workflows/ci.yml", "name": "CI"},
            f"{PREFIX}/actions/workflows/7/runs": [{"workflow_runs": [state.ci]}],
        })

    state.select = select
    select(sha)
    monkeypatch.setattr(release, "REPO_ROOT", root)
    monkeypatch.setattr(release, "VERSION_FILE", root / "VERSION")
    monkeypatch.setattr(release, "CHANGELOG_FILE", root / "CHANGELOG.md")
    monkeypatch.setattr(release, "BACKEND_DIR", root / "backend")
    monkeypatch.setattr(release.shutil, "which", lambda name: "/fixture/gh" if name == "gh" else None)

    def command(argv, **kwargs):
        assert isinstance(argv, list) and not kwargs.get("shell")
        state.calls.append(argv)
        if state.failure and argv[1] == state.failure:
            return subprocess.CompletedProcess(argv, 1, state.remote_url, state.remote_url)
        if argv[:4] == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(argv, 0, state.remote_url + "\n", "")
        if argv[0] == "/fixture/gh":
            if argv[1:3] == ["pr", "create"]:
                return subprocess.CompletedProcess(argv, 0, state.create_output, "")
            assert argv[1:6] == ["api", "--hostname", "github.com", "--method", "GET"]
            value = state.responses[argv[6]]
            if isinstance(value, subprocess.CompletedProcess):
                return value
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        if argv[0] == "docker":
            assert state.docker is not None, "测试未桩 docker 调用"
            return state.docker(argv)
        assert argv[0] == "git"
        if argv == ["git", "fetch", "origin"]:
            argv = [*argv, "refs/heads/main:refs/remotes/origin/main"]
        if argv[1] in {"fetch", "push", "ls-remote"}:
            assert "origin" in argv
            argv = [str(remote) if arg == "origin" else arg for arg in argv]
        return real_run(argv, **{**kwargs, "env": env})

    monkeypatch.setattr(release.subprocess, "run", command)
    return state


def writes(repo):
    return [cmd for cmd in repo.calls if cmd[:2] == ["git", "push"] or (
        cmd[:2] == ["git", "tag"] and "-l" not in cmd
    )]


def reject_tag(repo, capsys, message, args=None):
    with pytest.raises(SystemExit) as error:
        release.tag_main(args or [TAG, "--pr", "123", "--yes"])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert message in output.err
    assert "private-token" not in output.out + output.err
    assert not writes(repo)


@pytest.mark.parametrize("source", ["--pr", "--sha"])
def test_tag_pins_release_commit_after_main_advances(repo, source, capsys):
    tip = repo.sha
    for index in range(21):
        tip = repo.commit({"change.txt": str(index)}, tip)
    tip = repo.commit(version_files("0.2.0"), tip)
    assert release.tag_main([TAG, source, "123" if source == "--pr" else repo.sha, "--yes"]) == 0
    assert repo.git("rev-parse", f"{TAG}^{{commit}}") == repo.sha != tip
    assert repo.git("--git-dir", repo.remote, "rev-parse", f"{TAG}^{{commit}}") == repo.sha
    assert repo.git("rev-parse", TAG) != repo.sha
    assert (repo.root / "VERSION").read_text().strip() == "0.2.0"
    assert [cmd for cmd in repo.calls if cmd[:2] == ["git", "fetch"]] == [
        ["git", "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main"],
    ]
    assert all("--force" not in cmd for cmd in repo.calls)
    assert "private-token" not in str(capsys.readouterr())
    if source == "--sha":
        assert any(cmd[-2:] == ["--paginate", "--slurp"] for cmd in repo.calls)


@pytest.mark.parametrize("field,value", [
    ("state", "open"), ("merged_at", None), ("merged_at", ""), ("merged", False),
    ("number", 124), ("number", None), ("merge_commit_sha", None), ("merge_commit_sha", "abcdef"),
    ("title", "fix: release"),
    ("base", {"ref": "develop", "repo": {"full_name": REPO}}),
    ("base", {"ref": "main", "repo": {"full_name": None}}),
    ("head", {"ref": "feature/release", "repo": {"full_name": REPO}}),
    ("head", {"ref": f"release/{TAG}", "repo": {"full_name": "outsider/investring"}}),
    ("head", None),
])
def test_rejects_wrong_or_unmerged_release_pr(repo, capsys, field, value):
    repo.pr[field] = value
    reject_tag(repo, capsys, "不是已合并到 main")


@pytest.mark.parametrize("association", ["missing", "unrelated", "ordinary_commit", "ambiguous"])
def test_sha_requires_matching_release_pr_merge_commit(repo, capsys, association):
    sha = repo.sha
    if association == "missing":
        repo.responses[f"{PREFIX}/commits/{sha}/pulls"] = [[]]
    elif association == "unrelated":
        repo.pr["title"] = "chore: ordinary change"
    elif association == "ordinary_commit":
        sha = repo.commit({"change.txt": "after release"}, repo.sha)
        repo.responses[f"{PREFIX}/commits/{sha}/pulls"] = [[repo.pr]]
    else:
        repo.responses[f"{PREFIX}/commits/{sha}/pulls"] = [[repo.pr], [{**repo.pr, "number": 124}]]
    reject_tag(repo, capsys, "必须唯一对应", [TAG, "--sha", sha, "--yes"])


def test_rejects_commit_outside_main(repo, capsys):
    sha = repo.commit({"outside.txt": "not on origin/main"}, publish=False)
    repo.select(sha)
    reject_tag(repo, capsys, "不可从 origin/main 到达")


@pytest.mark.parametrize("field,value", [
    ("conclusion", "failure"), ("conclusion", "cancelled"), ("conclusion", None),
    ("status", "in_progress"), ("status", None), ("head_sha", "a" * 40),
    ("head_branch", "feature"), ("event", "pull_request"), ("event", "workflow_dispatch"),
    ("workflow_id", 8), ("path", ".github/workflows/impostor.yml"),
    ("head_repository", {"full_name": "outsider/investring"}), ("head_repository", None),
    ("head_repository", {"full_name": None}), ("id", None),
])
def test_rejects_failed_missing_or_untrusted_ci(repo, capsys, field, value):
    repo.ci[field] = value
    reject_tag(repo, capsys, "CI")


@pytest.mark.parametrize("workflow", [None, {}, {"id": 7, "name": "CI", "path": "wrong.yml"},
                                      {"id": 7, "name": "Other", "path": ".github/workflows/ci.yml"}])
def test_rejects_wrong_workflow_identity(repo, capsys, workflow):
    repo.responses[f"{PREFIX}/actions/workflows/ci.yml"] = workflow
    reject_tag(repo, capsys, "可信 CI workflow")


@pytest.mark.parametrize("runs", [[], [{"workflow_runs": []}], None, [{}], [{"workflow_runs": [None]}]])
def test_rejects_absent_or_malformed_ci_runs(repo, capsys, runs):
    repo.responses[f"{PREFIX}/actions/workflows/7/runs"] = runs
    reject_tag(repo, capsys, "CI")


def test_latest_ci_failure_cannot_fall_back_to_older_success(repo, capsys):
    repo.responses[f"{PREFIX}/actions/workflows/7/runs"] = [
        {"workflow_runs": [repo.ci, {**repo.ci, "id": 101, "conclusion": "failure"}]},
    ]
    reject_tag(repo, capsys, "最新的 main push CI 未成功完成")


def test_paginated_pr_and_ci_results(repo):
    repo.responses[f"{PREFIX}/commits/{repo.sha}/pulls"] = [[], [repo.pr]]
    repo.responses[f"{PREFIX}/actions/workflows/7/runs"] = [{"workflow_runs": []}, {"workflow_runs": [repo.ci]}]
    assert release.tag_main([TAG, "--sha", repo.sha, "--yes"]) == 0
    command = next(cmd for cmd in repo.calls if f"{PREFIX}/actions/workflows/7/runs" in cmd)
    assert command[7:] == [
        "-f", f"head_sha={repo.sha}", "-f", "branch=main", "-f", "event=push", "-f", "per_page=100",
        "--paginate", "--slurp",
    ]


@pytest.mark.parametrize("path", list(version_files()))
def test_tag_checks_every_version_projection_at_target(repo, capsys, path):
    sha = repo.commit({path: version_files("0.2.0")[path]}, repo.sha)
    repo.select(sha)
    reject_tag(repo, capsys, path)


def test_tag_checks_second_package_lock_version(repo, capsys):
    path = "frontend/package-lock.json"
    lock = json.loads((repo.root / path).read_text())
    lock["packages"][""]["version"] = "0.2.0"
    sha = repo.commit({path: json.dumps(lock, indent=2) + "\n"}, repo.sha)
    repo.select(sha)
    reject_tag(repo, capsys, path)


def test_same_annotated_tag_retry_is_noop_without_confirmation(repo, monkeypatch):
    assert release.tag_main([TAG, "--pr", "123", "--yes"]) == 0
    repo.calls.clear()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert release.tag_main([TAG, "--pr", "123"]) == 0
    assert not writes(repo)


@pytest.mark.parametrize("place", ["local", "remote", "both"])
@pytest.mark.parametrize("annotated", [False, True])
def test_existing_same_sha_tags_are_reused(repo, place, annotated):
    flags = ["-a", "-m", "existing tag"] if annotated else []
    if place in {"local", "both"}:
        repo.git("tag", *flags, TAG, repo.sha)
    if place in {"remote", "both"}:
        remote_flags = ["-a", "-m", "different annotation, same commit"] if annotated else []
        repo.git("--git-dir", repo.remote, "tag", *remote_flags, TAG, repo.sha)
    if place == "both" and annotated:
        assert repo.git("rev-parse", TAG) != repo.git("--git-dir", repo.remote, "rev-parse", TAG)
    assert release.tag_main([TAG, "--pr", "123", "--yes"]) == 0
    assert not any(cmd[:2] == ["git", "tag"] for cmd in repo.calls)
    assert len(writes(repo)) == (1 if place == "local" else 0)


@pytest.mark.parametrize("place", ["local", "remote"])
@pytest.mark.parametrize("annotated", [False, True])
def test_tag_conflict_never_overwrites_or_force_fetches(repo, capsys, place, annotated):
    other = repo.commit({"next.txt": "next"}, repo.sha)
    flags = ["-a", "-m", "conflicting tag"] if annotated else []
    for where in ("local", "remote"):
        prefix = ["--git-dir", repo.remote] if where == "remote" else []
        repo.git(*prefix, "tag", *flags, TAG, other if where == place else repo.sha)
    reject_tag(repo, capsys, "拒绝覆盖本地或远程标签")
    assert all("--force" not in cmd and "--tags" not in cmd for cmd in repo.calls if cmd[1] == "fetch")


def test_push_failure_preserves_local_tag_for_retry(repo, capsys):
    repo.failure = "push"
    with pytest.raises(SystemExit):
        release.tag_main([TAG, "--pr", "123", "--yes"])
    assert "private-token" not in str(capsys.readouterr())
    assert repo.git("rev-parse", f"{TAG}^{{commit}}") == repo.sha
    repo.calls.clear()
    repo.failure = None
    assert release.tag_main([TAG, "--pr", "123", "--yes"]) == 0
    assert [cmd[1] for cmd in writes(repo)] == ["push"]


@pytest.mark.parametrize("tty,answer", [(False, None), (True, "n"), (True, "y")])
def test_confirmation_boundary(repo, monkeypatch, capsys, tty, answer):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    if not tty:
        reject_tag(repo, capsys, "非交互环境请带 --yes", [TAG, "--pr", "123"])
    else:
        assert release.tag_main([TAG, "--pr", "123"]) == 0
        assert bool(writes(repo)) == (answer == "y")


@pytest.mark.parametrize("args", [
    [], [TAG], [TAG, "--sha", "a" * 40, "--pr", "123"], [TAG, "--sha", "abcdef"],
    [TAG, "--sha", "origin/main"], [TAG, "--sha", "a" * 40 + ";touch /tmp/no"],
    [TAG, "--pr", "0"], [TAG, "--pr", "-1"], [TAG, "--pr", "123;echo no"],
    ["v0.1.0-rc1", "--pr", "123"],
])
def test_bad_arguments_do_not_run_commands(repo, args):
    with pytest.raises(SystemExit) as error:
        release.tag_main(args)
    assert error.value.code != 0
    assert not repo.calls


@pytest.mark.parametrize("operation", ["api", "fetch", "ls-remote", "remote"])
def test_command_errors_do_not_print_credential_remote(repo, capsys, operation):
    repo.failure = operation
    reject_tag(repo, capsys, "失败")
    assert "private-token" not in str(capsys.readouterr())


@pytest.mark.parametrize("response", [None, {}, [], subprocess.CompletedProcess([], 0, "", ""),
                                      subprocess.CompletedProcess([], 0, "not JSON private-token", "")])
def test_missing_or_malformed_pr_response_fails_closed(repo, capsys, response):
    repo.responses[f"{PREFIX}/pulls/123"] = response
    reject_tag(repo, capsys, "PR")
    assert "private-token" not in str(capsys.readouterr())


def test_missing_gh_is_explicit(repo, monkeypatch, capsys):
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    reject_tag(repo, capsys, "未检测到 gh CLI")


def test_bad_remote_url_is_not_disclosed(repo, capsys):
    repo.remote_url = "https://user:private-token@untrusted.invalid/example/investring.git"
    reject_tag(repo, capsys, "origin 必须指向 github.com")
    assert "private-token" not in str(capsys.readouterr())


def test_doctor_is_readonly_without_git_application_or_generators(repo, monkeypatch):
    repo.git("tag", TAG, repo.sha)
    tip = repo.commit({"ordinary.txt": "ordinary HEAD reuses VERSION"}, repo.sha)
    assert repo.git("rev-parse", TAG) != tip
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.root.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("doctor must not write, execute subprocesses or load generators")

    monkeypatch.setattr(release.subprocess, "run", forbidden)
    for name in ("write_text", "regen_openapi", "verify_contracts", "venv_python"):
        monkeypatch.setattr(release, name, forbidden)
    for name in ("app", "app.main", "check_openapi", "openapi_runtime"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setenv("APP_VERSION", "unrelated-environment")
    monkeypatch.setattr(sys, "argv", ["release.py", "doctor"])
    assert release.main() == 0
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.root.rglob("*") if p.is_file()}
    assert before == after
    assert not repo.calls


@pytest.mark.parametrize("path", list(version_files()))
def test_doctor_detects_version_drift_without_writing(repo, path):
    (repo.root / path).write_bytes(version_files("0.2.0")[path].encode())
    before = {p: p.read_bytes() for p in repo.root.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as error:
        release.doctor_main([])
    assert error.value.code == 1
    assert before == {p: p.read_bytes() for p in repo.root.rglob("*") if p.is_file()}
    assert not repo.calls


@pytest.mark.parametrize("path,content", [
    ("VERSION", "v0.1.0\n"), ("VERSION", "bad\n"), ("backend/openapi.json", "invalid JSON"),
    ("backend/openapi.json", "null"), ("backend/openapi.json", "{}"),
    ("backend/openapi.json", '{"info": null}'), ("backend/pyproject.toml", "missing version line"),
    ("backend/openapi.json", None),
])
def test_doctor_handles_missing_or_malformed_projection(repo, path, content):
    target = repo.root / path
    if content is None:
        target.unlink()
    else:
        target.write_text(content)
    with pytest.raises(SystemExit) as error:
        release.doctor_main([])
    assert error.value.code == 1
    assert not repo.calls


def test_render_preserves_crlf_and_only_updates_project_versions(repo):
    edits = release.render_file_edits("0.2.0")
    assert len(edits) == 5
    for path, old, new in edits:
        assert path.read_bytes() == old.encode()
        if path.name in {"VERSION", "pyproject.toml"} and path.parent.name != "ir-cli":
            assert "\r\n" in new
    lock = json.loads(next(new for path, _, new in edits if path.name == "package-lock.json"))
    assert lock["version"] == lock["packages"][""]["version"] == "0.2.0"
    assert lock["packages"]["node_modules/dependency"]["version"] == "9.9.9"


@pytest.mark.parametrize("args", [["--suggest"], ["patch", "--dry-run"], ["--initial", "v0.1.0", "--dry-run"]])
def test_existing_preview_modes_do_not_write_or_generate(repo, monkeypatch, args):
    before = {path: (repo.root / path).read_bytes() for path in version_files()}

    def forbidden(*args, **kwargs):
        pytest.fail("preview must not write or generate contracts")

    for name in ("write_text", "regen_openapi", "verify_contracts", "venv_python"):
        monkeypatch.setattr(release, name, forbidden)
    monkeypatch.setattr(sys, "argv", ["release.py", *args])
    assert release.main() == 0
    assert before == {path: (repo.root / path).read_bytes() for path in version_files()}
    assert not writes(repo)


@pytest.mark.parametrize("kind,expected", [("major", "1.0.0"), ("minor", "0.2.0"), ("patch", "0.1.1")])
def test_bump_semantics(kind, expected):
    assert release.bump_version("0.1.0", kind) == expected


def test_release_pr_output_uses_identity_not_future_merge_sha(repo, capsys):
    release.create_release_pr(f"release/{TAG}", "0.1.0", "changelog", 540)
    command = next(cmd for cmd in repo.calls if cmd[1:3] == ["pr", "create"])
    assert command[3:9] == ["--repo", REPO, "--base", "main", "--head", f"release/{TAG}"]
    body = command[-1]
    assert "--pr <本PR号>" in body and "--sha <完整合并SHA>" in body and "fixes #540" in body
    assert repo.sha not in body and "立即" not in body
    output = capsys.readouterr().out
    assert f"{REPO}#123" in output and f"tag {TAG} --pr 123" in output and repo.sha not in output


@pytest.mark.parametrize("output", ["", "not a PR URL", "https://user:private-token@github.com/example/investring/pull/123"])
def test_create_pr_missing_identity_does_not_echo_untrusted_output(repo, capsys, output):
    repo.create_output = output
    with pytest.raises(SystemExit):
        release.create_release_pr(f"release/{TAG}", "0.1.0", "changelog", None)
    assert "private-token" not in str(capsys.readouterr())


# ------------------------------------------------------- alias（#540 语义镜像标签）
BACKEND_REPO = "registry.example.invalid/ns/investring-backend"
FRONTEND_REPO = "registry.example.invalid/ns/investring-frontend"
BACKEND_DIGEST = "sha256:" + "ab" * 32
FRONTEND_DIGEST = "sha256:" + "cd" * 32
NGINX_DIGEST = "sha256:" + "ef" * 32
BACKEND_TARGET = f"{BACKEND_REPO}:{TAG}"
FRONTEND_TARGET = f"{FRONTEND_REPO}:{TAG}"


def smoke_report(sha):
    """与 test_release_bundle.report_payload 同构；判据在 release_bundle._validated_smoke。"""
    return {
        "ok": True, "error": None, "expected_revision": sha,
        "images": {
            "backend": {"ref": f"{BACKEND_REPO}@{BACKEND_DIGEST}", "digest": BACKEND_DIGEST,
                        "id": "sha256:backend", "revision_label": sha},
            "frontend": {"ref": f"{FRONTEND_REPO}@{FRONTEND_DIGEST}", "digest": FRONTEND_DIGEST,
                         "id": "sha256:frontend", "revision_label": sha},
            "nginx": {"ref": "nginx:1.27-alpine", "digest": NGINX_DIGEST,
                      "digest_ref": f"nginx@{NGINX_DIGEST}", "id": "sha256:nginx"},
        },
        "bootstrap": {"state_before": "empty", "fingerprint_before": "fp-before",
                      "migration_fingerprint": "mig-fp", "expected_heads": ["0017"],
                      "dialect": "mysql", "fingerprint_after_prepare": "fp-after"},
        "checks": [], "cleanup": {},
    }


class FakeDocker:
    """模拟 docker CLI 与注册表实况。

    镜像身份以 `tags`（repo:tag → RepoDigests）为准，push 成功后才更新——与真实
    注册表一致：tag/push 之前的本地操作不改变远端事实。ids 为本地已有的
    digest 引用（pull 来源）。
    """

    def __init__(self, ids, tags=None, push_fail=()):
        self.ids = dict(ids)
        self.tags = dict(tags or {})
        self.push_fail = set(push_fail)
        self.calls = []
        self.pushed = []
        self.local = {}       # docker tag 的本地目标 → image Id
        self.source_of = {}   # image Id → 来源 digest 引用（inspect {{.Id}} 时登记）

    def __call__(self, argv):
        self.calls.append(argv)

        def done(rc, out="", err=""):
            return subprocess.CompletedProcess(argv, rc, out, err)

        op = argv[1]
        if op == "pull":
            ref = argv[2]
            return done(0) if ref in self.ids or ref in self.tags else done(1, "", "Error: not found")
        if op == "image":
            ref, fmt = argv[3], argv[5]
            if fmt == "{{json .RepoDigests}}":
                assert ref in self.tags, f"inspect 不存在的目标标签: {ref}"
                return done(0, json.dumps(self.tags[ref]))
            if fmt == "{{.Id}}":
                image_id = self.ids.get(ref)
                assert image_id, f"inspect 不存在的来源: {ref}"
                self.source_of[image_id] = ref
                return done(0, image_id + "\n")
        if op == "tag":
            self.local[argv[3]] = argv[2]
            return done(0)
        if op == "push":
            target = argv[2]
            if target in self.push_fail:
                return done(1, "", "denied: requested access to the resource is denied")
            self.tags[target] = [self.source_of[self.local[target]]]
            self.pushed.append(target)
            return done(0)
        raise AssertionError(f"意外的 docker 调用: {argv}")


@pytest.fixture
def alias_env(repo, tmp_path, monkeypatch):
    """构建指向 repo.sha 的发布包（run 100 = 夹具可信 CI run）、远程 v0.1.0 标签与 docker 桩。"""
    root = repo.root
    (root / "nginx").mkdir(parents=True, exist_ok=True)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "nginx" / "nginx.conf").write_text("events {}\n", encoding="utf-8")

    def build_bundle(out="bundle", *, run_id=100, sha=None):
        sha = sha or repo.sha
        report_path = tmp_path / f"report-{out}.json"
        report_path.write_text(json.dumps(smoke_report(sha)), encoding="utf-8")
        out_dir = tmp_path / out
        rc = rb.main(["build", "--smoke-report", str(report_path), "--sha", sha,
                      "--run-id", str(run_id), "--run-attempt", "1", "--workflow", "CI",
                      "--out-dir", str(out_dir), "--root", str(root)])
        assert rc == 0, "测试夹具：发布包构建失败"
        return out_dir

    repo.git("tag", TAG, repo.sha)
    repo.git("push", str(repo.remote), f"refs/tags/{TAG}")
    monkeypatch.setattr(release.shutil, "which",
                        lambda name: {"gh": "/fixture/gh", "docker": "/fixture/docker"}.get(name))
    docker = FakeDocker(ids={
        f"{BACKEND_REPO}@{BACKEND_DIGEST}": "sha256:" + "11" * 32,
        f"{FRONTEND_REPO}@{FRONTEND_DIGEST}": "sha256:" + "22" * 32,
    })
    repo.docker = docker
    return SimpleNamespace(repo=repo, docker=docker, bundle=build_bundle(), build_bundle=build_bundle)


def alias_argv(bundle, *extra):
    return [TAG, "--bundle", str(bundle), "--yes", *extra]


def reject_alias(env, capsys, message):
    with pytest.raises(SystemExit) as error:
        release.alias_main(alias_argv(env.bundle))
    assert error.value.code == 1
    output = capsys.readouterr()
    assert message in output.err
    assert "private-token" not in output.out + output.err
    assert not env.docker.calls and not env.docker.pushed


def test_alias_happy_path_tags_and_pushes_both_images(alias_env, capsys):
    assert release.alias_main(alias_argv(alias_env.bundle)) == 0
    assert alias_env.docker.pushed == [BACKEND_TARGET, FRONTEND_TARGET]
    tagged = [argv for argv in alias_env.docker.calls if argv[1] == "tag"]
    assert tagged == [
        ["docker", "tag", "sha256:" + "11" * 32, BACKEND_TARGET],
        ["docker", "tag", "sha256:" + "22" * 32, FRONTEND_TARGET],
    ]
    assert alias_env.docker.tags[BACKEND_TARGET] == [f"{BACKEND_REPO}@{BACKEND_DIGEST}"]
    out = capsys.readouterr().out
    assert f"[ok] {TAG}" in out and alias_env.repo.sha[:12] in out


def test_alias_dry_run_touches_nothing(alias_env, capsys):
    assert release.alias_main([TAG, "--bundle", str(alias_env.bundle), "--dry-run"]) == 0
    assert not alias_env.docker.calls and not alias_env.docker.pushed
    out = capsys.readouterr().out
    assert "[dry-run] 未改动注册表" in out and BACKEND_TARGET in out and FRONTEND_TARGET in out


def test_alias_is_idempotent_when_tag_already_matches(alias_env, capsys):
    alias_env.docker.tags = {
        BACKEND_TARGET: [f"{BACKEND_REPO}@{BACKEND_DIGEST}"],
        FRONTEND_TARGET: [f"{FRONTEND_REPO}@{FRONTEND_DIGEST}"],
    }
    assert release.alias_main(alias_argv(alias_env.bundle)) == 0
    assert not alias_env.docker.pushed
    assert capsys.readouterr().out.count("幂等，无需推送") == 2


def test_alias_refuses_to_overwrite_conflicting_tag(alias_env, capsys):
    alias_env.docker.tags = {BACKEND_TARGET: [f"{BACKEND_REPO}@sha256:" + "99" * 32]}
    with pytest.raises(SystemExit) as error:
        release.alias_main(alias_argv(alias_env.bundle))
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "拒绝覆盖语义标签" in output.err
    assert "private-token" not in output.out + output.err
    assert not alias_env.docker.pushed  # 冲突时已 pull 但未 tag/push 任何东西


def test_alias_retry_after_partial_success_pushes_missing_only(alias_env, capsys):
    alias_env.docker.push_fail.add(FRONTEND_TARGET)
    with pytest.raises(SystemExit) as error:
        release.alias_main(alias_argv(alias_env.bundle))
    assert error.value.code == 1
    assert alias_env.docker.pushed == [BACKEND_TARGET]

    alias_env.docker.push_fail.clear()
    capsys.readouterr()
    assert release.alias_main(alias_argv(alias_env.bundle)) == 0
    assert alias_env.docker.pushed == [BACKEND_TARGET, FRONTEND_TARGET]  # backend 不重复推送
    assert "幂等，无需推送" in capsys.readouterr().out


def test_alias_rejects_bundle_from_untrusted_run(alias_env, capsys):
    """run 99 可信但失败：最新 run 仍成功，只有 run 绑定判据能拦下它。"""
    failed = {**alias_env.repo.ci, "id": 99, "conclusion": "failure"}
    alias_env.repo.responses[f"{PREFIX}/actions/workflows/7/runs"] = [
        {"workflow_runs": [alias_env.repo.ci, failed]}]
    alias_env.bundle = alias_env.build_bundle("bundle-failed-run", run_id=99)
    reject_alias(alias_env, capsys, "拒绝以此包 alias")


def test_alias_requires_remote_tag_at_bundle_sha(alias_env, capsys):
    alias_env.repo.git("push", str(alias_env.repo.remote), f":refs/tags/{TAG}")
    reject_alias(alias_env, capsys, "请先完成 release.py tag 流程")


def test_alias_rejects_tampered_bundle(alias_env, capsys):
    (alias_env.bundle / "files" / "docker-compose.yml").write_text("services: {evil: true}\n",
                                                                    encoding="utf-8")
    reject_alias(alias_env, capsys, "发布包校验失败")


def test_alias_requires_docker_cli(alias_env, capsys, monkeypatch):
    monkeypatch.setattr(release.shutil, "which",
                        lambda name: "/fixture/gh" if name == "gh" else None)
    reject_alias(alias_env, capsys, "未检测到 docker CLI")


def test_alias_noninteractive_requires_yes(alias_env, capsys):
    with pytest.raises(SystemExit) as error:
        release.alias_main([TAG, "--bundle", str(alias_env.bundle)])
    assert error.value.code == 1
    assert "非交互环境请带 --yes" in capsys.readouterr().err
    assert not alias_env.docker.calls


def test_alias_interactive_decline_changes_nothing(alias_env, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert release.alias_main([TAG, "--bundle", str(alias_env.bundle)]) == 0
    assert "已取消" in capsys.readouterr().out
    assert not alias_env.docker.calls


def test_alias_rejects_invalid_version(alias_env, capsys):
    with pytest.raises(SystemExit) as error:
        release.alias_main(["v0.1", "--bundle", str(alias_env.bundle), "--yes"])
    assert error.value.code == 1
    assert "版本非法" in capsys.readouterr().err
    assert not alias_env.docker.calls


def test_main_dispatches_alias(alias_env, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["release.py", "alias", TAG, "--bundle", str(alias_env.bundle), "--dry-run"])
    assert release.main() == 0
    assert not alias_env.docker.calls
