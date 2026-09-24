#!/usr/bin/env python3
"""机器生成的最小可恢复发布包（issue #537 第四批 C）。

发布包回答一个问题：**「这次发布的确切制品与配置是什么，如何逐字节恢复」**。
配置摘要不能代替可恢复文件，因此包内同时携带：

  身份     完整 git SHA、构建身份（workflow / run id / attempt / 冒烟报告摘要）、
           VERSION——同 SHA 的不同构建由此可区分，且不可覆盖已生成的包目录。
  制品     前后端及 nginx 三个镜像的 digest 与可恢复拉取引用（repo@sha256:…），
           来自 smoke_images.py 的报告——只有冒烟通过（ok=true、MySQL、
           显式引导链完整）的构建才允许打包。
  数据库   迁移指纹与预期 heads（冒烟报告转述自镜像内 bootstrap status），
           供部署侧迁移授权比对（第四批 D）。
  配置     docker-compose.yml 与 nginx/nginx.conf 的**实际内容**及摘要；
           另生成 images.env（三个 digest 化镜像引用）与 manifest.sha256
           （sha256sum -c 兼容，服务器端无需 Python 即可校验传输完整性）。

verify 子命令逐字节重建期望的 bundle.json / images.env / manifest.sha256 并与
磁盘比对——任何字段篡改、文件替换或传输损坏都会现形，而不是只信 JSON 自报摘要。

退出码：0 通过；1 校验不一致（包内容与实际不符）；2 harness/用法错误
（输入缺失、冒烟未通过、参数非法——不假装是校验失败）。

用法：
    python3 scripts/release_bundle.py build \
        --smoke-report smoke-report.json --sha <40位SHA> \
        --run-id 123 --run-attempt 1 --out-dir release-bundle/
    python3 scripts/release_bundle.py verify --bundle-dir release-bundle/ [--sha <40位SHA>]

纯 stdlib（scripts/ 约定）；严格 JSON 解析复用 ci_gate.read_json，不抄第二份判据。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from ci_gate import GateError, read_json

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = 1
#: 发布包管理的配置文件（相对仓库根）。集合即判据：增减文件必须升 schema。
BUNDLE_FILES = ("docker-compose.yml", "nginx/nginx.conf")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_REF_RE = re.compile(r"^(?P<repo>[^\s@]+)@(?P<digest>sha256:[0-9a-f]{64})$")
#: manifest 覆盖的包内文件（相对 bundle 目录；bundle.json 自身也在内）
MANIFEST_PATHS = ("bundle.json", "images.env", *(f"files/{p}" for p in BUNDLE_FILES))


class BundleError(RuntimeError):
    """harness/用法错误：无法得出校验结论（exit 2）。"""


class BundleMismatch(RuntimeError):
    """校验不一致：包内容与其声明不符（exit 1）。"""


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _load_strict_json(path):
    """一次读取返回 (解析结果, 原文)：摘要针对读到的同一份字节，避免二次读取窗口。"""
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BundleError(f"无法读取 {path}: {exc}") from exc
    try:
        return read_json(text), text
    except GateError as exc:
        raise BundleError(f"{path} 不是合法 JSON: {exc}") from exc


def _require(cond, message):
    if not cond:
        raise BundleError(message)


def _mismatch(cond, message):
    if not cond:
        raise BundleMismatch(message)


# ---------------------------------------------------------------------- build
def _validated_smoke(report, sha):
    """从冒烟报告提取制品身份；报告不合格即拒绝打包（exit 2）。"""
    _require(isinstance(report, dict), "冒烟报告必须是 JSON 对象")
    _require(report.get("ok") is True,
             f"冒烟未通过（ok={report.get('ok')!r}），拒绝生成发布包")
    _require(report.get("expected_revision") == sha,
             f"冒烟报告 revision={report.get('expected_revision')!r} 与 --sha 不一致")
    images = report.get("images")
    _require(isinstance(images, dict), "冒烟报告缺少 images")
    out = {}
    for role in ("backend", "frontend"):
        entry = images.get(role)
        _require(isinstance(entry, dict), f"冒烟报告缺少 images.{role}")
        match = DIGEST_REF_RE.match(entry.get("ref") or "")
        _require(match is not None,
                 f"images.{role}.ref 不是 digest 引用（发布包只接受 repo@sha256:…）："
                 f"{entry.get('ref')!r}")
        _require(entry.get("digest") == match.group("digest"),
                 f"images.{role} 的 digest 字段与 ref 不一致")
        out[role] = {"repo": match.group("repo"), "digest": match.group("digest"),
                     "ref": entry["ref"]}
    nginx = images.get("nginx")
    _require(isinstance(nginx, dict), "冒烟报告缺少 images.nginx")
    _require(DIGEST_RE.match(nginx.get("digest") or "") is not None,
             f"images.nginx.digest 缺失或非法：{nginx.get('digest')!r}"
             "（本地缓存镜像无 RepoDigests；发布包只能来自注册表拉取的镜像）")
    _require((nginx.get("digest_ref") or "").endswith("@" + nginx["digest"]),
             f"images.nginx.digest_ref 与 digest 不一致：{nginx.get('digest_ref')!r}")
    out["nginx"] = {"ref": nginx.get("ref"), "digest": nginx["digest"],
                    "digest_ref": nginx["digest_ref"]}
    bootstrap = report.get("bootstrap")
    _require(isinstance(bootstrap, dict), "冒烟报告缺少 bootstrap")
    _require(bootstrap.get("dialect") == "mysql",
             f"发布链只接受 MySQL 冒烟（dialect={bootstrap.get('dialect')!r}）")
    _require(bootstrap.get("state_before") == "empty"
             and bootstrap.get("fingerprint_after_prepare"),
             "冒烟未走完显式引导链（status empty → prepare），拒绝打包")
    _require(isinstance(bootstrap.get("migration_fingerprint"), str)
             and bootstrap["migration_fingerprint"],
             "冒烟报告缺少 migration_fingerprint")
    heads = bootstrap.get("expected_heads")
    _require(isinstance(heads, list) and heads
             and all(isinstance(h, str) and h for h in heads),
             f"expected_heads 非法：{heads!r}")
    out["database"] = {"migration_fingerprint": bootstrap["migration_fingerprint"],
                       "expected_heads": list(heads)}
    return out


def _render_images_env(images):
    return (
        "# 由 scripts/release_bundle.py 生成——发布管理的镜像引用（非秘密文件）。\n"
        "# 三个镜像一律按 digest 固定：标签重指不会改变部署制品。\n"
        f"BACKEND_IMAGE_REF={images['backend']['ref']}\n"
        f"FRONTEND_IMAGE_REF={images['frontend']['ref']}\n"
        f"NGINX_IMAGE_REF={images['nginx']['digest_ref']}\n"
    )


def _render_manifest(entries):
    return "".join(f"{digest}  {path}\n" for path, digest in entries)


def build_bundle(args):
    sha = args.sha.lower()
    _require(SHA_RE.match(sha) is not None, f"--sha 不是 40 位十六进制提交号：{args.sha!r}")
    _require(args.run_id >= 1 and args.run_attempt >= 1, "run id/attempt 必须为正整数")
    _require(args.workflow.strip(), "--workflow 不能为空（构建身份的一部分）")

    root = Path(args.root)
    report, report_text = _load_strict_json(args.smoke_report)
    validated = _validated_smoke(report, sha)

    version_path = root / "VERSION"
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BundleError(f"无法读取 {version_path}: {exc}") from exc
    _require(version and not re.search(r"\s", version), f"VERSION 内容非法：{version!r}")

    files = {}
    for rel in BUNDLE_FILES:
        path = root / rel
        try:
            raw = path.read_bytes()
            content = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BundleError(f"无法读取配置文件 {path}: {exc}") from exc
        _require(raw.strip(), f"{path} 为空，拒绝打包")
        files[rel] = {"sha256": _sha256_bytes(raw), "content": content}

    bundle = {
        "schema": SCHEMA_VERSION,
        "git": {"sha": sha},
        "build": {
            "workflow": args.workflow.strip(),
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
            "smoke_report_sha256": f"sha256:{_sha256_bytes(report_text.encode('utf-8'))}",
        },
        "version": version,
        "images": {role: validated[role] for role in ("backend", "frontend", "nginx")},
        "database": validated["database"],
        "files": files,
    }
    bundle_text = json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    images_env = _render_images_env(bundle["images"])

    out = Path(args.out_dir)
    try:
        out.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise BundleError(
            f"{out} 已存在：发布包目录不可覆盖（同 SHA 的不同构建须使用不同目录，"
            "已选发布记录由服务器端保护）") from exc
    except OSError as exc:
        raise BundleError(f"无法创建 {out}: {exc}") from exc
    try:
        for rel in BUNDLE_FILES:
            dest = out / "files" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(files[rel]["content"].encode("utf-8"))
        (out / "images.env").write_text(images_env, encoding="utf-8")
        (out / "bundle.json").write_text(bundle_text, encoding="utf-8")
        entries = [(path, _sha256_bytes((out / path).read_bytes())) for path in MANIFEST_PATHS]
        (out / "manifest.sha256").write_text(_render_manifest(entries), encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"写入发布包失败: {exc}") from exc
    print(f"[bundle] 已生成 {out}: sha={sha} version={version} "
          f"run={args.run_id}.{args.run_attempt}")
    return 0


# --------------------------------------------------------------------- verify
def _validate_bundle(bundle, sha_arg):
    """结构校验全部走 BundleMismatch：包本身就是被验对象，不符即校验失败。"""
    _mismatch(isinstance(bundle, dict), "bundle.json 必须是 JSON 对象")
    _mismatch(set(bundle) == {"schema", "git", "build", "version", "images", "database", "files"},
              f"bundle.json 顶层键集合不符：{sorted(bundle)}")
    _mismatch(bundle.get("schema") == SCHEMA_VERSION,
              f"不支持的发布包 schema：{bundle.get('schema')!r}")
    git = bundle.get("git")
    _mismatch(isinstance(git, dict) and set(git) == {"sha"}
              and SHA_RE.match(git.get("sha") or "") is not None, f"git 字段非法：{git!r}")
    if sha_arg is not None:
        _mismatch(git["sha"] == sha_arg.lower(),
                  f"包内 sha={git['sha']} 与期望 {sha_arg.lower()} 不一致")
    build = bundle.get("build")
    _mismatch(isinstance(build, dict)
              and set(build) == {"workflow", "run_id", "run_attempt", "smoke_report_sha256"}
              and isinstance(build.get("workflow"), str) and build["workflow"]
              and type(build.get("run_id")) is int and build["run_id"] >= 1
              and type(build.get("run_attempt")) is int and build["run_attempt"] >= 1
              and DIGEST_RE.match(build.get("smoke_report_sha256") or "") is not None,
              f"build 字段非法：{build!r}")
    version = bundle.get("version")
    _mismatch(isinstance(version, str) and version and not re.search(r"\s", version),
              f"version 字段非法：{version!r}")
    images = bundle.get("images")
    _mismatch(isinstance(images, dict) and set(images) == {"backend", "frontend", "nginx"},
              f"images 键集合不符：{sorted(images) if isinstance(images, dict) else images!r}")
    for role in ("backend", "frontend"):
        entry = images.get(role)
        match = DIGEST_REF_RE.match((entry or {}).get("ref") or "") \
            if isinstance(entry, dict) else None
        _mismatch(isinstance(entry, dict) and set(entry) == {"repo", "digest", "ref"}
                  and match is not None and entry["repo"] == match.group("repo")
                  and entry["digest"] == match.group("digest"),
                  f"images.{role} 非法：{entry!r}")
    nginx = images.get("nginx")
    _mismatch(isinstance(nginx, dict) and set(nginx) == {"ref", "digest", "digest_ref"}
              and isinstance(nginx.get("ref"), str) and nginx["ref"]
              and DIGEST_RE.match(nginx.get("digest") or "") is not None
              and (nginx.get("digest_ref") or "").endswith("@" + nginx["digest"]),
              f"images.nginx 非法：{nginx!r}")
    database = bundle.get("database")
    _mismatch(isinstance(database, dict)
              and set(database) == {"migration_fingerprint", "expected_heads"}
              and isinstance(database.get("migration_fingerprint"), str)
              and database["migration_fingerprint"]
              and isinstance(database.get("expected_heads"), list)
              and database["expected_heads"]
              and all(isinstance(h, str) and h for h in database["expected_heads"]),
              f"database 字段非法：{database!r}")
    files = bundle.get("files")
    _mismatch(isinstance(files, dict) and set(files) == set(BUNDLE_FILES),
              f"files 键集合不符：{sorted(files) if isinstance(files, dict) else files!r}")
    for rel, entry in files.items():
        _mismatch(isinstance(entry, dict) and set(entry) == {"sha256", "content"}
                  and isinstance(entry.get("content"), str)
                  and entry.get("sha256") == _sha256_bytes(entry["content"].encode("utf-8")),
                  f"files[{rel}] 内容与其 sha256 不符（或字段缺失）")


def validate_bundle_dir(directory, sha=None):
    """校验并返回解析后的 bundle。release.py alias 与 verify 子命令共用同一判据。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise BundleError(f"发布包目录不存在：{directory}")
    bundle, bundle_text = _load_strict_json(directory / "bundle.json")
    try:
        images_env = (directory / "images.env").read_text(encoding="utf-8")
        manifest = (directory / "manifest.sha256").read_text(encoding="utf-8")
        on_disk = {rel: (directory / "files" / rel).read_bytes() for rel in BUNDLE_FILES}
    except (OSError, UnicodeDecodeError) as exc:
        raise BundleMismatch(f"发布包文件缺失或不可读：{exc}") from exc

    _validate_bundle(bundle, sha)
    for rel in BUNDLE_FILES:
        _mismatch(on_disk[rel] == bundle["files"][rel]["content"].encode("utf-8"),
                  f"files/{rel} 磁盘内容与 bundle.json 内嵌内容不一致")
    _mismatch(images_env == _render_images_env(bundle["images"]),
              "images.env 与包内镜像引用不一致（被改动或过期）")
    entries = [("bundle.json", _sha256_bytes(bundle_text.encode("utf-8"))),
               ("images.env", _sha256_bytes(images_env.encode("utf-8"))),
               *((f"files/{rel}", _sha256_bytes(on_disk[rel])) for rel in BUNDLE_FILES)]
    _mismatch(manifest == _render_manifest(entries),
              "manifest.sha256 与实际文件摘要不一致（传输损坏或被篡改）")
    return bundle


def verify_bundle(args):
    bundle = validate_bundle_dir(args.bundle_dir, args.sha)
    images = bundle["images"]
    print(f"[bundle] 校验通过: sha={bundle['git']['sha']} version={bundle['version']} "
          f"run={bundle['build']['run_id']}.{bundle['build']['run_attempt']} "
          f"backend={images['backend']['digest'][:19]}… "
          f"frontend={images['frontend']['digest'][:19]}… "
          f"nginx={images['nginx']['digest'][:19]}…")
    return 0


# ----------------------------------------------------------------------- CLI
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="从冒烟报告生成发布包")
    p_build.add_argument("--smoke-report", required=True, help="smoke_images.py --report 输出")
    p_build.add_argument("--sha", required=True, help="发布提交（40 位十六进制）")
    p_build.add_argument("--run-id", type=int, required=True)
    p_build.add_argument("--run-attempt", type=int, required=True)
    p_build.add_argument("--workflow", default="CI", help="构建来源 workflow 名")
    p_build.add_argument("--out-dir", required=True, help="输出目录（必须不存在）")
    p_build.add_argument("--root", default=str(ROOT), help="仓库根（测试注入用）")
    p_build.set_defaults(handler=build_bundle)

    p_verify = sub.add_parser("verify", help="逐字节校验发布包")
    p_verify.add_argument("--bundle-dir", required=True)
    p_verify.add_argument("--sha", help="可选：额外断言包内 git.sha")
    p_verify.set_defaults(handler=verify_bundle)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except BundleMismatch as exc:
        print(f"[bundle] 校验失败: {exc}", file=sys.stderr)
        return 1
    except BundleError as exc:
        print(f"[bundle] 错误: {exc}", file=sys.stderr)
        return 2
    except GateError as exc:  # 理论上已被 _load_strict_json 包裹，兜底不破语义
        print(f"[bundle] 错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
