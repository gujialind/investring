import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class StackError(RuntimeError):
    pass


def runtime_environment(backend_port: int, frontend_port: int, directory: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT", "WINDIR") if key in os.environ}
    environment.update(
        LANG="C.UTF-8",
        NEXT_TELEMETRY_DISABLED="1",
        NEXT_PUBLIC_API_URL="",
        API_BASE_URL=f"http://127.0.0.1:{backend_port}",
        BASE_URL=f"http://127.0.0.1:{frontend_port}",
        HOSTNAME="127.0.0.1",
        PORT=str(frontend_port),
        E2E_AUTH_FILE=str(directory / "auth.json"),
        E2E_OUTPUT_DIR=str(directory / "test-results"),
        E2E_SERVER_MANAGED="true",
        PLAYWRIGHT_HTML_OUTPUT_DIR=str(directory / "report"),
        PLAYWRIGHT_JSON_OUTPUT_FILE=str(directory / "playwright.json"),
        PLAYWRIGHT_HTML_OPEN="never",
    )
    return environment


@contextmanager
def build_lock(root: Path):
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    with (cache / "frontend-build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StackError("Another local build/verification owns this worktree") from exc
        yield


@contextmanager
def owned_process(command, *, cwd: Path, environment: dict[str, str], log: Path, shutdown_timeout: float = 10):
    with log.open("x", encoding="utf-8") as output:
        process = subprocess.Popen(
            command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            yield process
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=shutdown_timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def wait_up(process, url: str, *, timeout: float = 90):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StackError(f"Service exited before readiness (exit {process.returncode})")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200 and process.poll() is None:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.1)
    raise StackError(f"Service readiness timed out: {url}")


def check_dependencies(root: Path, environment):
    if not (root / "frontend/node_modules/.bin/next").is_file():
        raise StackError("Frontend dependencies missing; install the repository lockfile first")
    if not (root / "frontend/node_modules/.bin/playwright").is_file():
        raise StackError("Playwright dependency missing")
    for executable in ("node", "npm"):
        if shutil.which(executable, path=environment.get("PATH")) is None:
            raise StackError(f"Missing executable: {executable}")
    probe = subprocess.run(
        [sys.executable, "-I", "-c", "import uvicorn, fastapi, sqlalchemy"],
        env=environment, capture_output=True, check=False,
    )
    if probe.returncode:
        raise StackError("Backend dependencies missing; use the worktree Python environment")


def build_frontend(root: Path, environment, directory: Path):
    with owned_process(
        ["npm", "run", "build"], cwd=root / "frontend", environment=environment,
        log=directory / "build.log",
    ) as process:
        try:
            returncode = process.wait(timeout=600)
        except subprocess.TimeoutExpired as exc:
            raise StackError("Frontend build timed out") from exc
        if returncode != 0:
            raise StackError("Frontend build failed; no previous build will be started")
    frontend = root / "frontend"
    standalone = frontend / ".next/standalone"
    if not (standalone / "server.js").is_file():
        raise StackError("Build did not produce standalone/server.js")
    for source, destination in (
        (frontend / ".next/static", standalone / ".next/static"),
        (frontend / "public", standalone / "public"),
    ):
        if destination.is_symlink():
            raise StackError(f"Refusing a symlink in generated output: {destination}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    return standalone


def require_executed_tests(path: Path):
    from e2e_normalize import collect_rows

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = collect_rows(data, None)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, SystemExit) as exc:
        raise StackError(f"Cannot verify executed tests from {path}: {exc}") from exc
    executed = [row for row in rows if row[1] != "setup" and row[4] in {"passed", "failed", "timedOut", "interrupted"}]
    if not executed:
        raise StackError("No non-setup test executed; collection or skipped tests are not a passing verification")
    if data.get("errors") or any(row[3] == "unexpected" for row in rows):
        raise StackError("Playwright reported errors despite a successful exit")
    return len(executed)


def run_stack(args, forwarded, *, root=ROOT):
    parent = args.out.absolute() if args.out else root / ".cache/verification"
    environment = runtime_environment(args.backend_port, args.frontend_port, parent)
    check_dependencies(root, environment)
    with build_lock(root), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as frontend_lease:
        frontend_lease.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            frontend_lease.bind(("127.0.0.1", args.frontend_port))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as backend_probe:
                backend_probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                backend_probe.bind(("127.0.0.1", args.backend_port))
        except OSError as exc:
            raise StackError(f"Requested local port is occupied; existing services will not be reused: {exc}") from exc
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=f"{args.mode}-", dir=parent))
        environment = runtime_environment(args.backend_port, args.frontend_port, directory)
        print(f"Local verification artifacts: {directory}", flush=True)
        backend_command = [
            sys.executable, "-I", "-B", str(root / "backend/scripts/run_e2e_backend.py"),
            "--port", str(args.backend_port), "--database", str(directory / "e2e.db"),
        ]
        with owned_process(
            backend_command, cwd=root, environment=environment, log=directory / "backend.log",
        ) as backend:
            wait_up(backend, f"http://127.0.0.1:{args.backend_port}/health")
            standalone = build_frontend(root, environment, directory)
            frontend_lease.close()
            with owned_process(
                ["node", str(standalone / "server.js")], cwd=root / "frontend",
                environment=environment, log=directory / "frontend.log",
            ) as frontend:
                wait_up(frontend, environment["BASE_URL"])
                if args.mode == "e2e":
                    command = [str(root / "frontend/node_modules/.bin/playwright"), "test", *forwarded]
                else:
                    command = [
                        "node", str(root / "frontend/scripts/visual-shot.mjs"),
                        "--base", environment["BASE_URL"], "--out", str(directory / "screenshots"),
                        *forwarded,
                    ]
                with owned_process(
                    command, cwd=root / "frontend", environment=environment,
                    log=directory / f"{args.mode}.log",
                ) as check:
                    returncode = check.wait(timeout=900)
                if frontend.poll() is not None:
                    raise StackError("Owned frontend stopped during verification")
                if backend.poll() is not None:
                    raise StackError("Owned backend stopped during verification")
                if args.mode == "e2e" and returncode == 0:
                    count = require_executed_tests(directory / "playwright.json")
                    print(f"Executed non-setup tests: {count}")
                print(f"Local {args.mode}: {'pass' if returncode == 0 else 'fail'}; logs: {directory}")
                return 0 if returncode == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run local E2E or visual checks in a fresh owned stack")
    parser.add_argument("mode", choices=("e2e", "visual"))
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--out", type=Path, help="Parent directory for a new isolated result directory")
    args, forwarded = parser.parse_known_args(argv)
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    if (not 1 <= args.backend_port <= 65535 or not 1 <= args.frontend_port <= 65535
            or args.backend_port == args.frontend_port):
        parser.error("backend/frontend ports must be distinct and between 1 and 65535")
    forbidden = {"--base", "--out"} if args.mode == "visual" else {
        "--config", "-c", "--output", "--reporter", "--list", "--pass-with-no-tests",
        "--ui", "--ui-host", "--ui-port", "--debug", "--help", "-h",
    }
    if any(value.split("=", 1)[0] in forbidden or (args.mode == "e2e" and value.startswith("-c")) for value in forwarded):
        parser.error("forwarded options must preserve owned resources, reports and test execution; use the direct tool instead")

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return run_stack(args, forwarded)
    except (StackError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Local stack error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Local verification interrupted; owned processes stopped", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
