import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCOPES = ("backend", "frontend", "cli", "scripts", "e2e_morph")
STACKS = SCOPES[:-1]
EVENTS = {"pull_request", "push", "workflow_dispatch"}
CONTROL_FILES = {"scripts/ci_gate.py", "scripts/ci_policy.json", "scripts/tests/_ci_text.py"}
CONTROL_PREFIXES = (".github/workflows/", "scripts/tests/test_ci_")
RESULTS = {"success", "failure", "cancelled", "skipped"}


class GateError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise GateError(f"non-standard JSON constant: {value}")


def read_json(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise GateError(f"invalid JSON: {exc.msg}") from exc


def _string_list(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(item, str) and item for item in value)
            and len(value) == len(set(value)))


def validate_policy(policy):
    if (not isinstance(policy, dict) or set(policy) != {"version", "rules", "jobs"}
            or type(policy["version"]) is not int or policy["version"] != 1):
        raise GateError("unsupported policy structure/version")
    if not isinstance(policy["rules"], list) or not policy["rules"]:
        raise GateError("policy rules must be a nonempty list")
    for rule in policy["rules"]:
        if (not isinstance(rule, dict) or set(rule) != {"kind", "paths", "scopes", "reason"}
                or rule["kind"] not in ("prefix", "exact", "suffix")
                or not _string_list(rule["paths"], nonempty=True)
                or not _string_list(rule["scopes"])
                or not set(rule["scopes"]) <= set(SCOPES)
                or not isinstance(rule["reason"], str) or not rule["reason"].strip()):
            raise GateError("invalid policy rule")
    jobs = policy["jobs"]
    if not isinstance(jobs, dict) or any(jobs.get(job) != [] for job in ("changes", "docker-build-smoke")):
        raise GateError("changes and docker-build-smoke must always be required")
    for job, scopes in jobs.items():
        if (not re.fullmatch(r"[a-z][a-z0-9-]*", job) or job == "ci-ok"
                or not _string_list(scopes) or not set(scopes) <= set(SCOPES)):
            raise GateError(f"invalid job policy: {job}")
    return policy


def matching_rules(policy, path):
    return [rule for rule in policy["rules"] if any(
        path.startswith(pattern) if rule["kind"] == "prefix" else
        path.endswith(pattern) if rule["kind"] == "suffix" else path == pattern
        for pattern in rule["paths"]
    )]


def is_control(path):
    return path in CONTROL_FILES or path.startswith(CONTROL_PREFIXES)


def plan_changes(policy, paths, event, *, control_changed=False):
    if event not in EVENTS:
        raise GateError(f"unsupported event: {event}")
    reasons = {scope: [] for scope in SCOPES}

    def require(scopes, reason):
        for scope in scopes:
            if reason not in reasons[scope]:
                reasons[scope].append(reason)

    if event != "pull_request":
        require(STACKS, f"full validation for {event}")
        return reasons
    for path in paths:
        rules = matching_rules(policy, path)
        for rule in rules:
            require(rule["scopes"], f"{rule['reason']}: {path!r}")
        if not rules:
            require(STACKS, f"unmapped path: {path!r}")
        if is_control(path):
            require(STACKS, f"CI control change: {path!r}")
    if control_changed:
        require(STACKS, "CI controls differ between base and head")
    if not paths:
        require(STACKS, "empty PR diff: conservative full validation")
    return reasons


def scope_outputs(reasons):
    return {scope: str(bool(reasons[scope])).lower() for scope in SCOPES}


def _git(root, *args, env=None):
    proc = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True)
    if proc.returncode:
        detail = proc.stderr.decode(errors="replace").strip()[:200]
        raise GateError(f"git {args[0]} failed (exit {proc.returncode}): {detail}")
    return proc.stdout


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise GateError("missing or invalid event commit SHA")
    return value


def event_changes(root, env):
    event = env["GITHUB_EVENT_NAME"]
    if event not in EVENTS:
        raise GateError(f"unsupported event: {event}")
    payload = read_json(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GateError("event payload must be an object")
    try:
        head = _sha(payload["pull_request"]["head"]["sha"] if event == "pull_request" else env["GITHUB_SHA"])
        base = _sha(payload["pull_request"]["base"]["sha"]) if event == "pull_request" else None
    except (KeyError, TypeError) as exc:
        raise GateError("missing event head/base identity") from exc
    if _git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise GateError("checkout HEAD does not match event head SHA")
    if base is None:
        return event, [], False

    def paths(*revisions):
        raw = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                   "--name-only", "-z", *revisions, "--")
        return [os.fsdecode(path) for path in raw.split(b"\0") if path]

    changed = paths(f"{base}...{head}")
    # Three-dot diff omits policy updates made only on the base branch.
    control_changed = any(is_control(path) for path in paths(base, head))
    return event, changed, control_changed


def aggregate(policy, reasons, needs):
    if not isinstance(needs, dict) or set(needs) != set(policy["jobs"]):
        actual = set(needs) if isinstance(needs, dict) else set()
        raise GateError(f"needs job IDs mismatch: missing={sorted(set(policy['jobs']) - actual)}, "
                        f"extra={sorted(actual - set(policy['jobs']))}")
    for job, record in needs.items():
        if (not isinstance(record, dict) or not isinstance(record.get("result"), str)
                or record["result"] not in RESULTS):
            raise GateError(f"invalid result for {job}")
    if needs["changes"]["result"] != "success":
        raise GateError(f"changes must succeed, got {needs['changes']['result']}")
    if needs["changes"].get("outputs") != scope_outputs(reasons):
        raise GateError("changes outputs are missing, invalid or disagree with recomputed impact")
    print("job | required | actual | verdict | reason")
    passed = True
    for job, scopes in policy["jobs"].items():
        active = [scope for scope in scopes if reasons[scope]]
        required = not scopes or bool(active)
        result = needs[job]["result"]
        allowed = result == "success" or (not required and result == "skipped")
        if not scopes:
            reason = "always required"
        elif active:
            sources = list(dict.fromkeys(reason for scope in active for reason in reasons[scope]))
            reason = "; ".join(sources[:3])
            if len(sources) > 3:
                reason += f"; +{len(sources) - 3} more impact matches"
        else:
            reason = f"not applicable: no impact in {', '.join(scopes)}"
        print(f"{job} | {required} | {result} | {'pass' if allowed else 'fail'} | {reason}")
        passed &= allowed
    return passed


def main():
    parser = argparse.ArgumentParser(description="Compute CI impact and verify required job results")
    parser.add_argument("command", choices=("detect", "aggregate"))
    args = parser.parse_args()
    try:
        root = Path.cwd()
        policy = validate_policy(read_json((root / "scripts/ci_policy.json").read_text(encoding="utf-8")))
        event, paths, control_changed = event_changes(root, os.environ)
        reasons = plan_changes(policy, paths, event, control_changed=control_changed)
        if args.command == "aggregate":
            return 0 if aggregate(policy, reasons, read_json(os.environ["CI_NEEDS"])) else 1
        with Path(os.environ["GITHUB_OUTPUT"]).open("a+", encoding="utf-8") as output:
            output.seek(0)
            if any(line.partition("=")[0] in SCOPES for line in output.read().splitlines()):
                raise GateError("scope output already exists in GITHUB_OUTPUT")
            output.write("".join(f"{key}={value}\n" for key, value in scope_outputs(reasons).items()))
        return 0
    except (GateError, OSError, KeyError, UnicodeError) as exc:
        print(f"CI gate error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
