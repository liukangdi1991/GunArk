import argparse
import sys
import shutil
from pathlib import Path

from trendradar.infrastructure.runtime import storage_root, source_root
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


RESET_PATHS = [
    "app.db",
    "objects/",
    "market/",
    "cache/",
]


def cmd_init_v2(args: argparse.Namespace) -> None:
    root = storage_root()
    objects_root = root / "objects"
    market_root = root / "market"
    bars_root = market_root / "bars"

    if args.reset_runtime:
        if not args.confirm_reset:
            print("ERROR: --reset-runtime requires --confirm-reset")
            sys.exit(1)

        old_db_dir = source_root() / "db"

        print("Will delete and recreate:")
        for rel in RESET_PATHS:
            full = root / rel
            if full.exists():
                print(f"  DELETE: {full}")
            else:
                print(f"  SKIP (not exists): {full}")
        if old_db_dir.exists():
            print(f"  DELETE: {old_db_dir}")

        # Delete
        for rel in RESET_PATHS:
            full = root / rel
            if full.is_dir():
                shutil.rmtree(full)
            elif full.exists():
                full.unlink()
        if old_db_dir.exists():
            shutil.rmtree(old_db_dir)

    # Create dirs
    bars_root.mkdir(parents=True, exist_ok=True)
    objects_root.mkdir(parents=True, exist_ok=True)
    (objects_root / "jobs").mkdir(exist_ok=True)

    # Init DB
    sc = StorageConnection(root)
    conn = sc.connect()
    init_schema(conn)
    conn.close()

    # Bootstrap default strategy group with all registered strategies
    from trendradar.app.services.strategy_service import ensure_default_group

    ensure_default_group(sc)

    print(f"V2 storage initialized at {root}")


def main():
    parser = argparse.ArgumentParser(prog="trendradar")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init-v2", help="Initialize V2 runtime data")
    init.add_argument("--reset-runtime", action="store_true", help="Delete old runtime data first")
    init.add_argument("--confirm-reset", action="store_true", help="Confirm data deletion")

    args = parser.parse_args()
    if args.command == "init-v2":
        cmd_init_v2(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
