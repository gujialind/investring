import argparse
import json
import os
from pathlib import Path
import signal
import sys

import ci_gate
import verify

ROOT = Path(__file__).resolve().parents[1]


def evaluate(payload, base: str, *, root=ROOT):
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        raise ValueError("Expected a Stop event object")
    if type(payload.get("stop_hook_active", False)) is not bool:
        raise ValueError("stop_hook_active must be a boolean")
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("Stop event is missing cwd")
    event_root = verify.git(Path(cwd), "rev-parse", "--show-toplevel").decode().strip()
    if Path(event_root).resolve() != root.resolve():
        raise ValueError("Stop event and adapter belong to different worktrees")
    configured_root = os.environ.get("QODER_PROJECT_DIR")
    if configured_root and Path(configured_root).resolve() != root.resolve():
        raise ValueError("QODER_PROJECT_DIR does not match this adapter's worktree")
    impact = verify.plan(root, base)
    paths = impact["source"]["changed_paths"]
    if (not paths or all(path.endswith(".md") for path in paths)
            or "cli-contract-check" not in impact["required_ci_jobs"]):
        return {
            "check": "contract", "status": "not_applicable",
            "reason": "No non-document change requires the existing CI contract job; other verification is not implied",
        }
    return verify.run_check(root, "contract", [], base)


def respond(result, continuation: bool) -> int:
    status = result["status"]
    if status in {"pass", "not_applicable"}:
        detail = f"contract: {status}. {result.get('reason', 'Declared contract checks completed.')}"
        print(json.dumps({
            "decision": "allow", "reason": detail,
            "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": detail},
        }, ensure_ascii=False))
        return 0
    detail = {
        "check": "contract", "status": status, "task_state": "unverified",
        "reason": result.get("reason", "Verification did not complete"),
        "result_file": result.get("result_file"),
        "next_step": (
            "Inspect contract differences and generate only when warranted; this hook never modifies contracts or commits."
            if status == "fail" else
            "Repair the execution environment or report blocked; this is not evidence of contract drift."
        ),
    }
    print(json.dumps(detail, ensure_ascii=False), file=sys.stderr)
    # A continued Stop may end with an error, never with a fabricated passing result.
    return 1 if continuation else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Qoder Stop adapter; validates contracts without installing hooks")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    payload = None

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        payload = ci_gate.read_json(sys.stdin.read())
        result = evaluate(payload, args.base)
    except (OSError, ValueError, ci_gate.GateError) as exc:
        result = {"status": "error", "reason": str(exc)}
    except KeyboardInterrupt:
        result = {"status": "error", "reason": "Contract verification interrupted"}
    finally:
        signal.signal(signal.SIGTERM, previous)
    continuation = isinstance(payload, dict) and payload.get("stop_hook_active") is True
    return respond(result, continuation)


if __name__ == "__main__":
    sys.exit(main())
