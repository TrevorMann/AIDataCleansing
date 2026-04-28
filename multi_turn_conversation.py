"""Interactive REPL for the data cleaning system.

Thin wrapper around cleaning.run_cleaning_workflow + cleaning.AdHocConversation.
All business logic lives in the cleaning/ subpackage.
"""
import os
import sys

from cleaning import AdHocConversation, build_clients, run_cleaning_workflow
from config import DB_PATH
from database import init_db


def _load_env() -> None:
    """Read .env into os.environ without requiring python-dotenv."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v


def _read_multiline(prompt: str) -> str:
    print(f"\n{prompt}")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def _print_help() -> None:
    print("Commands:")
    print("  CLEAN [query]   - run a cleaning workflow (e.g. 'CLEAN canadian data', 'CLEAN all')")
    print("  HISTORY         - show conversation history")
    print("  HELP            - this message")
    print("  QUIT            - exit")
    print("Anything else is sent to the ad-hoc data assistant.")


def main() -> None:
    _load_env()
    init_db(DB_PATH)
    clients = build_clients()
    convo = AdHocConversation(clients=clients, db_path=DB_PATH)

    print("=" * 70)
    print("DATA CLEANING REPL")
    print("=" * 70)
    _print_help()

    turn = 0
    while True:
        turn += 1
        cmd = _read_multiline(
            f"Turn {turn} (type 'END' on a new line to submit, or QUIT to exit):"
        )
        upper = cmd.strip().upper()

        if upper == "QUIT" or upper == "EXIT":
            print("Goodbye.")
            return
        if upper == "HISTORY":
            convo.show_history()
            turn -= 1
            continue
        if upper == "HELP":
            _print_help()
            turn -= 1
            continue
        if upper.startswith("CLEAN"):
            query = cmd[5:].strip()
            print(f"\n{'=' * 70}\nCLEANING WORKFLOW: {query or '(no query)'}\n{'=' * 70}")
            report = run_cleaning_workflow(query, clients=clients, db_path=DB_PATH)
            print(report.summary_text)
            if report.flag_summary:
                print(f"\nFlags raised ({len(report.flag_summary)}):")
                for f in report.flag_summary[:20]:
                    print(f"  - record {f['raw_data_id']} [{f['severity']}] "
                          f"{f['flag_type']}: {f['reason']}")
                if len(report.flag_summary) > 20:
                    print(f"  ... and {len(report.flag_summary) - 20} more")
            if report.errors:
                print(f"\nErrors ({len(report.errors)}):")
                for e in report.errors:
                    print(f"  - record {e['raw_data_id']}: {e['error']}")
            continue
        if not cmd.strip():
            print("Please enter a message or command.")
            turn -= 1
            continue

        print("\n[Processing...]")
        print("\nASSISTANT:")
        print(convo.send(cmd))


if __name__ == "__main__":
    main()
