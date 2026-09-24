from contextlib import contextmanager, ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/local_stack.py"


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("_local_stack_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ports():
    with socket.socket() as backend, socket.socket() as frontend:
        backend.bind(("127.0.0.1", 0))
        frontend.bind(("127.0.0.1", 0))
        return backend.getsockname()[1], frontend.getsockname()[1]


@contextmanager
def child_process(code, *args):
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, *map(str, args)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def wait_for_file(path, process):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if path.exists():
            return
        assert process.poll() is None, "fixture process exited before handshake"
        threading.Event().wait(0.01)
    pytest.fail(f"fixture process did not create {path.name}")


@contextmanager
def http_server(*, status=200, before_response=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if before_response:
                before_response()
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/health", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_runtime_environment_binds_ports_and_artifacts_without_parent_overrides(stack, monkeypatch, tmp_path):
    poison = {key: f"fixture-parent-{key}" for key in (
        "DATABASE_URL", "APP_VERSION", "SECRET_KEY", "TUSHARE_TOKEN", "GITHUB_TOKEN",
        "PYTHONPATH", "NODE_OPTIONS", "HTTP_PROXY", "CI", "NEXT_PUBLIC_API_URL",
        "API_BASE_URL", "BASE_URL", "PORT", "E2E_AUTH_FILE", "E2E_OUTPUT_DIR",
        "E2E_SERVER_MANAGED", "PLAYWRIGHT_HTML_OUTPUT_DIR", "PLAYWRIGHT_JSON_OUTPUT_FILE",
    )}
    system = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(tmp_path / "home"),
              "SYSTEMROOT": "/fixture-system", "WINDIR": "/fixture-windows"}
    for key, value in {**poison, **system}.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    first = stack.runtime_environment(18001, 13001, tmp_path / "first")
    second = stack.runtime_environment(18002, 13002, tmp_path / "second")
    for environment, backend_port, frontend_port, directory in (
        (first, 18001, 13001, tmp_path / "first"),
        (second, 18002, 13002, tmp_path / "second"),
    ):
        assert environment == {
            **system, "LANG": "C.UTF-8", "NEXT_TELEMETRY_DISABLED": "1",
            "NEXT_PUBLIC_API_URL": "", "API_BASE_URL": f"http://127.0.0.1:{backend_port}",
            "BASE_URL": f"http://127.0.0.1:{frontend_port}", "HOSTNAME": "127.0.0.1",
            "PORT": str(frontend_port), "E2E_AUTH_FILE": str(directory / "auth.json"),
            "E2E_OUTPUT_DIR": str(directory / "test-results"), "E2E_SERVER_MANAGED": "true",
            "PLAYWRIGHT_HTML_OUTPUT_DIR": str(directory / "report"),
            "PLAYWRIGHT_JSON_OUTPUT_FILE": str(directory / "playwright.json"),
            "PLAYWRIGHT_HTML_OPEN": "never",
        }
    assert dict(os.environ) == before
    assert not (tmp_path / "first").exists()


def test_build_lock_excludes_other_processes_and_releases_after_error(stack, tmp_path):
    other_root = tmp_path / "other-worktree"
    other_root.mkdir()
    code = '''
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("stack", sys.argv[1])
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)
try:
    with stack.build_lock(Path(sys.argv[2])):
        print("acquired")
except stack.StackError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
'''

    def probe(root):
        return subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(SCRIPT), str(root)],
            env={"PATH": os.environ.get("PATH", os.defpath)}, cwd=tmp_path,
            capture_output=True, text=True, timeout=3, check=False,
        )

    with pytest.raises(RuntimeError, match="fixture failure"):
        with stack.build_lock(tmp_path):
            blocked = probe(tmp_path)
            assert blocked.returncode == 2
            assert "Another local build/verification" in blocked.stderr
            assert probe(other_root).returncode == 0
            raise RuntimeError("fixture failure")
    acquired = probe(tmp_path)
    assert acquired.returncode == 0 and acquired.stdout.strip() == "acquired"


GROUP_CHILD = '''
from pathlib import Path
import signal, sys
marker = Path(sys.argv[1])
def stop(signum, frame):
    marker.write_text(str(signum), encoding="utf-8")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
print("ready", flush=True)
while True:
    signal.pause()
'''

GROUP_PARENT = '''
from pathlib import Path
import json, os, signal, subprocess, sys
ready, stopped, reaped = map(Path, sys.argv[1:4])
def stop(signum, frame):
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
child = subprocess.Popen([sys.executable, "-I", "-B", "-c", sys.argv[4], str(stopped)],
                         stdout=subprocess.PIPE, text=True)
try:
    assert child.stdout.readline().strip() == "ready"
    ready.with_suffix(".tmp").write_text(json.dumps({"pid": os.getpid(), "pgid": os.getpgrp(), "child": child.pid}), encoding="utf-8")
    ready.with_suffix(".tmp").replace(ready)
    while True:
        signal.pause()
finally:
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=1)
    reaped.write_text(str(child.returncode), encoding="utf-8")
'''


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_owned_process_cleans_its_group_only_on_error_or_interrupt(stack, monkeypatch, tmp_path, failure):
    ready, stopped, reaped = (tmp_path / name for name in ("ready.json", "stopped", "reaped"))
    command = [sys.executable, "-I", "-B", "-c", GROUP_PARENT, str(ready), str(stopped), str(reaped), GROUP_CHILD]
    signals, owned_pids = [], set()
    real_killpg = os.killpg

    def guarded_killpg(pgid, signum):
        assert pgid in owned_pids, "attempted to signal a group not created by this test"
        signals.append((pgid, signum))
        real_killpg(pgid, signum)

    monkeypatch.setattr(stack.os, "killpg", guarded_killpg)
    process = None
    with child_process("import signal; signal.pause()") as unrelated:
        try:
            with pytest.raises(failure):
                with stack.owned_process(
                    command, cwd=tmp_path, environment=stack.runtime_environment(18000, 13000, tmp_path),
                    log=tmp_path / "owned.log",
                ) as process:
                    owned_pids.add(process.pid)
                    wait_for_file(ready, process)
                    group = json.loads(ready.read_text(encoding="utf-8"))
                    assert group["pid"] == group["pgid"] == process.pid
                    assert os.getpgid(group["child"]) == process.pid
                    assert os.getpgid(unrelated.pid) != process.pid
                    raise failure("fixture interruption")
            assert process.returncode == 0
            assert stopped.read_text(encoding="utf-8") == str(signal.SIGTERM)
            assert reaped.read_text(encoding="utf-8") == "0"
            with pytest.raises(ProcessLookupError):
                os.kill(group["child"], 0)
            assert signals == [(process.pid, signal.SIGTERM)]
            assert unrelated.poll() is None
        finally:
            if process is not None and process.poll() is None:
                real_killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    real_killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)


def test_owned_process_reaps_nonzero_exit(stack, tmp_path):
    with stack.owned_process(
        [sys.executable, "-I", "-B", "-c", "raise SystemExit(7)"], cwd=tmp_path,
        environment=stack.runtime_environment(18000, 13000, tmp_path), log=tmp_path / "failed.log",
    ) as process:
        assert process.wait(timeout=2) == 7
    assert process.returncode == 7
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)


@pytest.mark.parametrize("phase", ["before-request", "during-request"])
def test_wait_up_does_not_accept_other_http_200_after_owned_process_exit(stack, phase):
    with child_process("import signal; signal.pause()") as process:
        def stop():
            process.terminate()
            process.wait(timeout=2)

        if phase == "before-request":
            stop()
        with http_server(before_response=stop if phase == "during-request" else None) as (url, requests):
            with pytest.raises(stack.StackError, match="Service exited before readiness"):
                stack.wait_up(process, url, timeout=0.5)
            assert bool(requests) == (phase == "during-request")


@pytest.mark.parametrize("status", [200, 503])
def test_wait_up_requires_live_child_and_http_200(stack, status):
    with child_process("import signal; signal.pause()") as process, http_server(status=status) as (url, requests):
        if status == 200:
            assert stack.wait_up(process, url, timeout=0.3) is None
        else:
            with pytest.raises(stack.StackError, match="readiness timed out"):
                stack.wait_up(process, url, timeout=0.15)
        assert requests and process.poll() is None


@pytest.mark.parametrize("occupied", ["backend", "frontend"])
def test_run_stack_refuses_occupied_ports_before_artifacts_or_processes(stack, monkeypatch, tmp_path, occupied):
    check, build, owned = Mock(), Mock(), Mock()
    monkeypatch.setattr(stack, "check_dependencies", check)
    monkeypatch.setattr(stack, "build_frontend", build)
    monkeypatch.setattr(stack, "owned_process", owned)
    with socket.socket() as listener, socket.socket() as unused:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        unused.bind(("127.0.0.1", 0))
        occupied_port, free_port = listener.getsockname()[1], unused.getsockname()[1]
        unused.close()
        args = SimpleNamespace(
            mode="e2e", out=tmp_path / "artifacts",
            backend_port=occupied_port if occupied == "backend" else free_port,
            frontend_port=occupied_port if occupied == "frontend" else free_port,
        )
        with pytest.raises(stack.StackError, match="port is occupied"):
            stack.run_stack(args, [], root=tmp_path)
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
    assert not args.out.exists()
    build.assert_not_called()
    owned.assert_not_called()


def test_recently_closed_backend_is_not_reported_as_an_existing_service(stack, monkeypatch, tmp_path, ports):
    address = ("127.0.0.1", ports[0])
    with socket.socket() as listener, socket.socket() as client:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(address)
        listener.listen()
        client.connect(address)
        accepted, _ = listener.accept()
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
        client.close()
        accepted.close()
    with socket.socket() as plain_probe:
        with pytest.raises(OSError):
            plain_probe.bind(address)
    monkeypatch.setattr(stack, "check_dependencies", Mock())
    owned = Mock(side_effect=RuntimeError("port checks passed"))
    monkeypatch.setattr(stack, "owned_process", owned)
    args = SimpleNamespace(mode="e2e", out=tmp_path / "artifacts", backend_port=ports[0], frontend_port=ports[1])
    with pytest.raises(RuntimeError, match="port checks passed"):
        stack.run_stack(args, [], root=tmp_path)
    owned.assert_called_once()


def playwright_test(project="chromium", status="expected", results=("passed",)):
    test = {"projectName": project, "status": status}
    if results is not None:
        test["results"] = [{"status": result} for result in results]
    return test


def playwright_report(*tests, errors=()):
    return {
        "suites": [{
            "file": "e2e/fixture.spec.ts",
            "suites": [{"title": "nested suite", "specs": [
                {"title": f"fixture case {index}", "tests": [test]}
                for index, test in enumerate(tests)
            ]}],
        }],
        "stats": {status: sum(test["status"] == status for test in tests)
                  for status in ("expected", "unexpected", "flaky", "skipped")},
        "errors": list(errors),
    }


@pytest.mark.parametrize("mode", ["e2e", "visual"])
@pytest.mark.parametrize("exitcode", [0, 7])
def test_run_stack_wires_owned_commands_environment_and_exit_status(stack, monkeypatch, tmp_path, ports, mode, exitcode):
    (tmp_path / "frontend").mkdir()
    args = SimpleNamespace(mode=mode, out=tmp_path / "artifacts", backend_port=ports[0], frontend_port=ports[1])
    forwarded = ["--project=chromium", "--grep", "fixture case"] if mode == "e2e" else ["--path", "/dashboard", "--device", "mobile"]
    starts, stops, builds = [], [], []
    check, wait = Mock(), Mock()
    monkeypatch.setattr(stack, "check_dependencies", check)
    monkeypatch.setattr(stack, "wait_up", wait)

    @contextmanager
    def owned(command, **kwargs):
        def finish(timeout):
            if mode == "e2e" and exitcode == 0:
                report = Path(kwargs["environment"]["PLAYWRIGHT_JSON_OUTPUT_FILE"])
                report.write_text(json.dumps(playwright_report(playwright_test())), encoding="utf-8")
            return exitcode

        process = SimpleNamespace(wait=Mock(side_effect=finish), poll=Mock(return_value=None))
        starts.append((command, kwargs, process))
        if len(starts) > 1:
            assert builds, "a service/test started before the build"
            with socket.socket() as released:
                released.bind(("127.0.0.1", args.frontend_port))
        if len(starts) == 3:
            assert wait.call_args_list == [
                call(starts[0][2], f"http://127.0.0.1:{ports[0]}/health"),
                call(starts[1][2], kwargs["environment"]["BASE_URL"]),
            ], "checks must not start before both owned services are ready"
        try:
            yield process
        finally:
            stops.append(command)

    def build(root, environment, directory):
        wait.assert_called_once_with(starts[0][2], f"http://127.0.0.1:{ports[0]}/health")
        with socket.socket() as competitor:
            with pytest.raises(OSError):
                competitor.bind(("127.0.0.1", args.frontend_port))
        builds.append((root, environment, directory))
        return root / "frontend/.next/standalone"

    monkeypatch.setattr(stack, "owned_process", owned)
    monkeypatch.setattr(stack, "build_frontend", build)
    assert stack.run_stack(args, forwarded, root=tmp_path) == (0 if exitcode == 0 else 1)
    directory, = args.out.iterdir()
    assert directory.name.startswith(mode + "-")
    environment = stack.runtime_environment(*ports, directory)
    assert builds == [(tmp_path, environment, directory)]
    check.assert_called_once_with(tmp_path, stack.runtime_environment(*ports, args.out))
    assert starts[0][0] == [
        sys.executable, "-I", "-B", str(tmp_path / "backend/scripts/run_e2e_backend.py"),
        "--port", str(ports[0]), "--database", str(directory / "e2e.db"),
    ]
    assert starts[0][1] == {"cwd": tmp_path, "environment": environment, "log": directory / "backend.log"}
    assert len(starts) == 3
    for _, options, _ in starts[1:]:
        assert options["environment"] == environment and options["cwd"] == tmp_path / "frontend"
    assert environment["E2E_SERVER_MANAGED"] == "true"
    assert starts[1][0] == ["node", str(tmp_path / "frontend/.next/standalone/server.js")]
    assert starts[1][1]["log"] == directory / "frontend.log"
    if mode == "e2e":
        assert starts[2][0] == [str(tmp_path / "frontend/node_modules/.bin/playwright"), "test", *forwarded]
    else:
        assert starts[2][0] == [
            "node", str(tmp_path / "frontend/scripts/visual-shot.mjs"),
            "--base", environment["BASE_URL"], "--out", str(directory / "screenshots"), *forwarded,
        ]
    assert starts[2][1]["log"] == directory / f"{mode}.log"
    starts[2][2].wait.assert_called_once_with(timeout=900)
    starts[0][2].wait.assert_not_called()
    starts[1][2].wait.assert_not_called()
    assert wait.call_args_list == [
        call(starts[0][2], f"http://127.0.0.1:{ports[0]}/health"),
        call(starts[1][2], environment["BASE_URL"]),
    ]
    assert stops == [entry[0] for entry in reversed(starts)]


@pytest.mark.parametrize("report,error,count", [
    pytest.param(None, "Cannot verify executed tests", None, id="missing-report"),
    pytest.param("not JSON", "Cannot verify executed tests", None, id="invalid-json"),
    pytest.param([], "Cannot verify executed tests", None, id="invalid-shape"),
    pytest.param(playwright_report(), "Cannot verify executed tests", None, id="no-tests"),
    pytest.param(playwright_report(playwright_test(status="skipped", results=("skipped",))),
                 "No non-setup test executed", None, id="all-skipped"),
    pytest.param(playwright_report(playwright_test(project="setup")),
                 "No non-setup test executed", None, id="setup-only"),
    pytest.param(playwright_report(playwright_test(project="setup"),
                                   playwright_test(status="skipped", results=("skipped",))),
                 "No non-setup test executed", None, id="setup-and-skips"),
    pytest.param(playwright_report(playwright_test(results=None)),
                 "No non-setup test executed", None, id="collection-without-results"),
    pytest.param(playwright_report(playwright_test(results=())),
                 "Cannot verify executed tests", None, id="empty-results"),
    pytest.param(playwright_report(playwright_test(), errors=[{"message": "worker crashed"}]),
                 "Playwright reported errors", None, id="report-errors"),
    pytest.param(playwright_report(playwright_test(status="unexpected", results=("failed",))),
                 "Playwright reported errors", None, id="unexpected-failure"),
    pytest.param(playwright_report(playwright_test(status="unexpected")),
                 "Playwright reported errors", None, id="unexpected-pass"),
    pytest.param({**playwright_report(playwright_test()), "stats": {"expected": 2}},
                 "Cannot verify executed tests.*stats", None, id="stats-mismatch"),
    pytest.param(playwright_report(playwright_test()), None, 1, id="one-executed-test"),
    pytest.param(playwright_report(
        playwright_test(project="setup"), playwright_test(),
        playwright_test(project="mobile", status="skipped", results=("skipped",)),
        playwright_test(project="custom-project", status="flaky", results=("failed", "passed")),
    ), None, 2, id="count-tests-not-setup-skips-or-attempts"),
])
def test_run_stack_requires_execution_report_even_after_zero_exit(
    stack, monkeypatch, tmp_path, ports, capsys, report, error, count,
):
    args = SimpleNamespace(mode="e2e", out=tmp_path / "artifacts", backend_port=ports[0], frontend_port=ports[1])
    monkeypatch.setattr(stack, "check_dependencies", Mock())
    monkeypatch.setattr(stack, "build_frontend", Mock(return_value=tmp_path / "frontend/.next/standalone"))
    monkeypatch.setattr(stack, "wait_up", Mock())
    starts, stops = [], []

    @contextmanager
    def owned(command, **kwargs):
        def finish(timeout):
            if report is not None:
                path = Path(kwargs["environment"]["PLAYWRIGHT_JSON_OUTPUT_FILE"])
                path.write_text(report if isinstance(report, str) else json.dumps(report), encoding="utf-8")
            return 0

        process = SimpleNamespace(wait=Mock(side_effect=finish), poll=Mock(return_value=None))
        role = kwargs["log"].stem
        starts.append((role, process))
        try:
            yield process
        finally:
            stops.append(role)

    monkeypatch.setattr(stack, "owned_process", owned)
    if error:
        with pytest.raises(stack.StackError, match=error):
            stack.run_stack(args, [], root=tmp_path)
        output = capsys.readouterr().out
        assert "Local e2e: pass" not in output
        assert "Executed non-setup tests:" not in output
    else:
        assert stack.run_stack(args, [], root=tmp_path) == 0
        output = capsys.readouterr().out
        assert f"Executed non-setup tests: {count}\n" in output
        assert "Local e2e: pass" in output
    assert [role for role, _ in starts] == ["backend", "frontend", "e2e"]
    starts[-1][1].wait.assert_called_once_with(timeout=900)
    assert stops == ["e2e", "frontend", "backend"]


@pytest.mark.parametrize("mode", ["e2e", "visual"])
def test_failed_build_never_starts_old_artifacts(stack, monkeypatch, tmp_path, ports, mode):
    standalone = tmp_path / "frontend/.next/standalone"
    standalone.mkdir(parents=True)
    old_server = standalone / "server.js"
    old_server.write_bytes(b"fixture-old-build")
    monkeypatch.setattr(stack, "check_dependencies", Mock())
    monkeypatch.setattr(stack, "wait_up", Mock())
    real_owned = stack.owned_process
    starts, processes = [], []

    @contextmanager
    def owned(command, **kwargs):
        starts.append(command)
        if command[0] == "npm":
            assert command == ["npm", "run", "build"]
            code = "raise SystemExit(7)"
        else:
            assert len(starts) == 1 and command[3].endswith("run_e2e_backend.py"), "old output was started"
            code = "import signal; signal.pause()"
        with real_owned([sys.executable, "-I", "-B", "-c", code], **kwargs) as process:
            processes.append(process)
            yield process

    monkeypatch.setattr(stack, "owned_process", owned)
    args = SimpleNamespace(mode=mode, out=tmp_path / "artifacts", backend_port=ports[0], frontend_port=ports[1])
    try:
        with pytest.raises(stack.StackError, match="build failed; no previous build will be started"):
            stack.run_stack(args, [], root=tmp_path)
        assert len(starts) == 2 and all(process.poll() is not None for process in processes)
        assert old_server.read_bytes() == b"fixture-old-build"
        directory, = args.out.iterdir()
        assert (directory / "build.log").exists() and (directory / "backend.log").exists()
        assert not (directory / "e2e.log").exists() and not (directory / "frontend.log").exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


@pytest.mark.parametrize("mode,forwarded", [
    ("e2e", ["--project=chromium", "--grep", "fixture case", "--workers=1"]),
    ("visual", ["--path", "/dashboard", "--device", "mobile"]),
])
@pytest.mark.parametrize("separator", [[], ["--"]])
def test_main_forwards_tool_arguments_without_rewriting(stack, monkeypatch, tmp_path, mode, forwarded, separator):
    run = Mock(return_value=1)
    monkeypatch.setattr(stack, "run_stack", run)
    assert stack.main([
        mode, "--backend-port", "18000", "--frontend-port", "13000", "--out", str(tmp_path),
        *separator, *forwarded,
    ]) == 1
    args, actual_forwarded = run.call_args.args
    assert (args.mode, args.backend_port, args.frontend_port, args.out) == (mode, 18000, 13000, tmp_path)
    assert actual_forwarded == forwarded


@pytest.mark.parametrize("options", [
    ["--backend-port", "0"], ["--frontend-port", "-1"], ["--backend-port", "65536"],
    ["--frontend-port", "65536"], ["--backend-port", "not-a-port"],
    ["--backend-port", "13000", "--frontend-port", "13000"],
])
def test_main_rejects_invalid_or_equal_ports_before_starting(stack, monkeypatch, options):
    run = Mock()
    monkeypatch.setattr(stack, "run_stack", run)
    with pytest.raises(SystemExit) as error:
        stack.main(["e2e", *options])
    assert error.value.code == 2
    run.assert_not_called()


@pytest.mark.parametrize("mode,forwarded", [
    ("e2e", ["--config", "fixture.config.ts"]),
    ("e2e", ["--config=fixture.config.ts"]),
    ("e2e", ["-c", "fixture.config.ts"]),
    ("e2e", ["-cfixture.config.ts"]),
    ("e2e", ["-c=fixture.config.ts"]),
    ("e2e", ["--output", "elsewhere"]),
    ("e2e", ["--output=elsewhere"]),
    ("e2e", ["--reporter", "json"]),
    ("e2e", ["--reporter=line"]),
    ("e2e", ["--list"]),
    ("e2e", ["--list=true"]),
    ("e2e", ["--pass-with-no-tests"]),
    ("e2e", ["--pass-with-no-tests=true"]),
    ("e2e", ["--ui"]),
    ("e2e", ["--ui=true"]),
    ("e2e", ["--ui-host", "127.0.0.1"]),
    ("e2e", ["--ui-host=127.0.0.1"]),
    ("e2e", ["--ui-port", "19999"]),
    ("e2e", ["--ui-port=19999"]),
    ("e2e", ["--debug"]),
    ("e2e", ["--debug=true"]),
    ("e2e", ["--help"]),
    ("e2e", ["-h"]),
    ("e2e", ["--project=chromium", "--reporter=line"]),
    ("visual", ["--base", "http://127.0.0.1:19999"]),
    ("visual", ["--base=http://127.0.0.1:19999"]),
    ("visual", ["--out", "elsewhere"]),
    ("visual", ["--out=elsewhere"]),
])
def test_main_refuses_forwarded_owned_address_config_or_output_overrides(stack, monkeypatch, mode, forwarded):
    run = Mock(return_value=0)
    monkeypatch.setattr(stack, "run_stack", run)
    with pytest.raises(SystemExit) as error:
        stack.main([mode, "--", *forwarded])
    assert error.value.code == 2
    run.assert_not_called()


def test_sigterm_exits_130_and_cleans_only_the_owned_child(tmp_path):
    ready = tmp_path / "signal-ready.json"
    code = '''
import importlib.util, json, os, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("stack", sys.argv[1])
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)
directory, ready = Path(sys.argv[2]), Path(sys.argv[3])
def run(args, forwarded):
    with stack.owned_process(
        [sys.executable, "-I", "-B", "-c", "import signal; signal.pause()"],
        cwd=directory, environment={"PATH": os.defpath}, log=directory / "signal.log",
    ) as child:
        assert os.getpgid(child.pid) == child.pid
        ready.with_suffix(".tmp").write_text(json.dumps({"pid": child.pid}), encoding="utf-8")
        ready.with_suffix(".tmp").replace(ready)
        while True:
            signal.pause()
stack.run_stack = run
raise SystemExit(stack.main(["e2e"]))
'''
    owned_pid = None
    with child_process("import signal; signal.pause()") as unrelated:
        try:
            with child_process(code, SCRIPT, tmp_path, ready) as owner:
                wait_for_file(ready, owner)
                owned_pid = json.loads(ready.read_text(encoding="utf-8"))["pid"]
                assert os.getpgid(unrelated.pid) != owned_pid
                owner.send_signal(signal.SIGTERM)
                assert owner.wait(timeout=3) == 130
                with pytest.raises(ProcessLookupError):
                    os.kill(owned_pid, 0)
                assert unrelated.poll() is None
        finally:
            if owned_pid is not None:
                try:
                    os.killpg(owned_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize("mode", ["e2e", "visual"])
def test_run_stack_interrupt_cleans_independent_frontend_without_stopping_other_stack(tmp_path, mode):
    code = '''
from contextlib import contextmanager
from functools import partial
import importlib.util, sys
from pathlib import Path
script, directory, mode, backend_port, frontend_port, parent_code, child_code = sys.argv[1:]
directory = Path(directory)
spec = importlib.util.spec_from_file_location("stack", script)
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)
real_owned = stack.owned_process
@contextmanager
def owned(command, **kwargs):
    role = kwargs["log"].stem
    simulated = [sys.executable, "-I", "-B", "-c", parent_code,
                 str(directory / f"{role}-ready.json"),
                 str(directory / f"{role}-stopped"),
                 str(directory / f"{role}-reaped"), child_code]
    try:
        with real_owned(simulated, **kwargs) as process:
            yield process
    finally:
        with (directory / "stops").open("a", encoding="utf-8") as stops:
            stops.write(role + "\\n")
stack.owned_process = owned
stack.check_dependencies = lambda *args: None
stack.build_frontend = lambda root, environment, output: root / "frontend/.next/standalone"
stack.wait_up = lambda *args: None
stack.run_stack = partial(stack.run_stack, root=directory)
raise SystemExit(stack.main([mode, "--backend-port", backend_port, "--frontend-port", frontend_port]))
'''
    directories = [tmp_path / "first-stack", tmp_path / "second-stack"]
    for directory in directories:
        (directory / "frontend").mkdir(parents=True)
    with ExitStack() as reservations:
        sockets = [reservations.enter_context(socket.socket()) for _ in range(4)]
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        port_numbers = [listener.getsockname()[1] for listener in sockets]

    groups = []
    try:
        with child_process(code, SCRIPT, directories[0], mode, *port_numbers[:2], GROUP_PARENT, GROUP_CHILD) as first, \
                child_process(code, SCRIPT, directories[1], mode, *port_numbers[2:], GROUP_PARENT, GROUP_CHILD) as second:
            for owner, directory in zip((first, second), directories):
                for role in ("backend", "frontend", mode):
                    ready = directory / f"{role}-ready.json"
                    wait_for_file(ready, owner)
                    group = json.loads(ready.read_text(encoding="utf-8"))
                    groups.append(group)
                    assert group["pid"] == group["pgid"] != owner.pid
                    assert os.getpgid(group["child"]) == group["pgid"]
            assert len({group["pgid"] for group in groups}) == 6

            first.send_signal(signal.SIGTERM)
            assert first.wait(timeout=5) == 130
            assert (directories[0] / "stops").read_text(encoding="utf-8").splitlines() == [mode, "frontend", "backend"]
            for role, group in zip(("backend", "frontend", mode), groups[:3]):
                assert (directories[0] / f"{role}-stopped").read_text(encoding="utf-8") == str(signal.SIGTERM)
                assert (directories[0] / f"{role}-reaped").read_text(encoding="utf-8") == "0"
                for pid in (group["pid"], group["child"]):
                    with pytest.raises(ProcessLookupError):
                        os.kill(pid, 0)

            assert second.poll() is None
            assert not (directories[1] / "stops").exists()
            for role, group in zip(("backend", "frontend", mode), groups[3:]):
                assert os.getpgid(group["pid"]) == os.getpgid(group["child"]) == group["pgid"]
                assert not (directories[1] / f"{role}-stopped").exists()
                assert not (directories[1] / f"{role}-reaped").exists()
            second.send_signal(signal.SIGTERM)
            assert second.wait(timeout=5) == 130
            assert (directories[1] / "stops").read_text(encoding="utf-8").splitlines() == [mode, "frontend", "backend"]
            for group in groups[3:]:
                for pid in (group["pid"], group["child"]):
                    with pytest.raises(ProcessLookupError):
                        os.kill(pid, 0)
    finally:
        for group in groups:
            try:
                os.killpg(group["pgid"], signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("failure,exitcode", [
    (OSError("fixture OS error"), 2),
    (subprocess.TimeoutExpired("fixture", 0.1), 2),
    (KeyboardInterrupt(), 130),
])
def test_main_reports_failure_and_restores_signal_handler(stack, monkeypatch, failure, exitcode):
    monkeypatch.setattr(stack, "run_stack", Mock(side_effect=failure))
    before = signal.getsignal(signal.SIGTERM)
    assert stack.main(["e2e"]) == exitcode
    assert signal.getsignal(signal.SIGTERM) == before
