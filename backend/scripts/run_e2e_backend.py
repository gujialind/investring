import argparse
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
from contextlib import asynccontextmanager

BACKEND_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def _noop_lifespan(app):
    yield


def isolated_environment(directory: Path, database: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    environment.update(
        HOME=str(directory),
        TMPDIR=str(directory),
        LANG="C.UTF-8",
        DATABASE_URL=f"sqlite:///{database}",
        SECRET_KEY=secrets.token_hex(32),
        SCHEDULER_ENABLED="false",
        AKSHARE_ENABLED="false",
        DEBUG="false",
    )
    return environment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start an isolated, freshly seeded local E2E backend")
    parser.add_argument("--port", type=int, default=os.environ.get("E2E_PORT", "8000"))
    parser.add_argument("--database", default=os.environ.get("E2E_DB_PATH"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    explicit_database = Path(args.database).absolute() if args.database else None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", args.port))
            listener.listen(2048)
        except OSError as exc:
            parser.exit(2, f"Cannot own backend port {args.port}: {exc}\n")
        with tempfile.TemporaryDirectory(prefix="investring-e2e-") as temporary:
            directory = Path(temporary)
            database = explicit_database or directory / "e2e.db"
            try:
                descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except OSError as exc:
                parser.exit(2, f"Cannot create a new E2E database at {database}: {exc}\n")
            os.close(descriptor)
            environment = isolated_environment(directory, database)
            os.environ.clear()
            os.environ.update(environment)
            os.chdir(directory)
            sys.path.insert(0, str(BACKEND_DIR))

            from app.main import app
            from app.database import SessionLocal, engine
            from app.models.base import Base
            from app.init_tasks import init_scheduled_tasks
            from tests.seed_base import seed_base_data, seed_e2e_active
            import uvicorn

            try:
                Base.metadata.create_all(bind=engine)
                with SessionLocal() as db:
                    init_scheduled_tasks(db)
                    seed_base_data(db)
                    seed_e2e_active(db)
                app.router.lifespan_context = _noop_lifespan
                print(f"E2E backend: http://127.0.0.1:{args.port}; database: {database}", flush=True)
                server = uvicorn.Server(uvicorn.Config(
                    app, host="127.0.0.1", port=args.port, log_level="warning", log_config=None,
                ))
                server.run(sockets=[listener])
                return 0 if server.started else 1
            finally:
                engine.dispose()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
