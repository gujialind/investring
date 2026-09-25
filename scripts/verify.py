import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import ci_gate
from local_stack import owned_process

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ("contract", "e2e", "visual")
_CHECK_CODE = """
import runpy, sys, traceback
from pathlib import Path
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except Exception:
    traceback.print_exc()
    sys.exit(2)
"""


def git(root, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return ci_gate._git(root, *args, env=environment)


def changed_paths(root, *revisions):
    raw = git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", *revisions, "--")
    return {os.fsdecode(path) for path in raw.split(b"\0") if path}


def snapshot(root: Path, base: str):
    actual_root = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if actual_root != root.resolve():
        raise ci_gate.GateError("Verification root does not match the actual Git worktree")
    base_sha = git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").decode().strip()
    head = git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    committed = changed_paths(root, f"{base_sha}...{head}")
    working = changed_paths(root, "HEAD") | changed_paths(root, "--cached", "HEAD")
    untracked = {
        os.fsdecode(path) for path in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if path
    }
    fingerprint = hashlib.sha256(head.encode())
    for args in (("HEAD",), ("--cached", "HEAD")):
        fingerprint.update(git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", *args, "--"))
    for relative in sorted(untracked):
        path = root / relative
        fingerprint.update(os.fsencode(relative) + b"\0")
        if path.is_symlink():
            fingerprint.update(os.fsencode(os.readlink(path)))
        else:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(65536), b""):
                    fingerprint.update(chunk)
        fingerprint.update(b"\0")
    return {
        "base": base_sha, "head": head,
        "changed_paths": sorted(committed | working | untracked),
        "working_paths": sorted(working | untracked),
        "fingerprint": fingerprint.hexdigest(),
    }


def plan(root: Path, base: str):
    state = snapshot(root, base)
    policy = ci_gate.validate_policy(ci_gate.read_json((root / "scripts/ci_policy.json").read_text(encoding="utf-8")))
    controls = any(ci_gate.is_control(path) for path in changed_paths(root, state["base"], state["head"]))
    reasons = ci_gate.plan_changes(policy, state["changed_paths"], "pull_request", control_changed=controls)
    required = [job for job, scopes in policy["jobs"].items() if not scopes or any(reasons[scope] for scope in scopes)]
    return {
        "kind": "plan", "worktree": str(root), "source": state, "scopes": reasons,
        "required_ci_jobs": required, "available_local_checks": list(CHECKS),
        "guidance": "AGENTS.md#2-按任务加载",
        "limitations": "This is impact guidance, not verification. Local checks do not replace required CI, business tests or review.",
    }


def commands_for(root: Path, check: str, arguments):
    if check == "contract":
        if arguments:
            raise ValueError("contract takes no forwarded arguments; it only checks and never generates")
        return [
            [sys.executable, "-I", "-c", _CHECK_CODE, str(root / "backend/check_openapi.py")],
            [sys.executable, "-I", "-c", _CHECK_CODE, str(root / "ir-cli/scripts/gen_response_fields.py"), "--check"],
        ]
    if check not in CHECKS:
        raise ValueError(f"Unsupported check: {check}")
    return [[sys.executable, str(root / "scripts/local_stack.py"), check, *arguments]]


def run_check(root: Path, check: str, arguments, base: str):
    commands = commands_for(root, check, arguments)
    before = snapshot(root, base)
    parent = root / ".cache/verification"
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f"{check}-", dir=parent))
    result = {
        "kind": "result", "check": check, "status": "error", "cwd": str(root),
        "scope": "OpenAPI and CLI response-field consistency" if check == "contract" else {"mode": check, "arguments": arguments},
        "started_at": datetime.now(timezone.utc).isoformat(), "source": before,
        "commands": [], "result_file": str(directory / "result.json"),
        "limitations": "Only this execution and its declared scope were checked; this is not whole-task completion or reusable CI evidence.",
    }
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT", "WINDIR") if key in os.environ}
    environment.update(LANG="C.UTF-8", PYTHONDONTWRITEBYTECODE="1")
    try:
        for index, command in enumerate(commands):
            entry = {"argv": command, "cwd": str(root), "log": str(directory / f"{index + 1}.log"), "exit_code": None}
            result["commands"].append(entry)
            # The local runner may reap three groups sequentially, each with a 10-second grace period.
            with owned_process(
                command, cwd=root, environment=environment, log=Path(entry["log"]),
                shutdown_timeout=10 if check == "contract" else 40,
            ) as process:
                entry["exit_code"] = process.wait(timeout=120 if check == "contract" else 1800)
            if entry["exit_code"] != 0:
                result["status"] = "fail" if entry["exit_code"] == 1 else "error"
                result["reason"] = "Check reported a mismatch/failure" if result["status"] == "fail" else "Check could not complete"
                break
        else:
            result["status"] = "pass"
        result["source_after"] = snapshot(root, base)
        if result["source_after"]["fingerprint"] != before["fingerprint"]:
            result["status"] = "error"
            result["reason"] = "Working tree changed during execution; the current state is unverified"
    except (OSError, ValueError, ci_gate.GateError, subprocess.TimeoutExpired) as exc:
        result["status"] = "error"
        result["reason"] = str(exc)
    except KeyboardInterrupt:
        result["status"] = "error"
        result["reason"] = "Verification interrupted; owned processes stopped"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path(result["result_file"]).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Thin local verification entry point; CI policy remains authoritative")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--base", default="origin/main")
        subparser.add_argument("--json", action="store_true")
        if name == "run":
            subparser.add_argument("check", choices=CHECKS)
    args, forwarded = parser.parse_known_args(argv)
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    if args.command == "plan" and forwarded:
        parser.error(f"Unknown plan arguments: {forwarded}")

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        value = plan(ROOT, args.base) if args.command == "plan" else run_check(ROOT, args.check, forwarded, args.base)
        code = 0 if args.command == "plan" else {"pass": 0, "fail": 1, "error": 2}[value["status"]]
    except (OSError, ValueError, ci_gate.GateError) as exc:
        value, code = {"kind": args.command, "status": "error", "reason": str(exc)}, 2
    except KeyboardInterrupt:
        value, code = {"kind": args.command, "status": "error", "reason": "Interrupted"}, 2
    finally:
        signal.signal(signal.SIGTERM, previous)
    if args.json or args.command == "plan":
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(f"{value.get('check', args.command)}: {value['status']}")
        if "reason" in value:
            print(value["reason"])
        if "result_file" in value:
            print(f"Result and log references: {value['result_file']}")
        print(value.get("limitations", ""))
    return code


if __name__ == "__main__":
    sys.exit(main())
