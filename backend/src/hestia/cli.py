"""
Administration from the gateway's shell (`hestia --help`).

    hestia create-user alice --role admin     first admin (prompts for a password)
    hestia reset-password alice
    hestia verify-journal                      exit code 0 = intact, 1 = tampered
    hestia export-journal journal.jsonl        full copy for archiving
    hestia check-models                        ML files match their fingerprints
    hestia new-secret                          print a fresh random secret for .env
    hestia test-email                          send a test alert e-mail (alerts must be on)
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys

from hestia.security.auth import Role, UserStore
from hestia.settings import get_settings
from hestia.storage.db import Database
from hestia.trust.journal import EventKind, SafetyJournal


def _ask_password() -> str:
    first = getpass.getpass("Password (12+ characters): ")
    if first != getpass.getpass("Repeat: "):
        sys.exit("Passwords do not match")
    return first


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hestia", description="Hestia gateway administration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-user", help="create a user")
    p.add_argument("username")
    p.add_argument("--role", choices=["viewer", "operator", "admin"], default="viewer")
    p.add_argument("--name", default="")
    p = sub.add_parser("reset-password", help="set a new password (ends the user's sessions)")
    p.add_argument("username")
    sub.add_parser("verify-journal", help="verify the safety journal's hash chain and signatures")
    p = sub.add_parser("export-journal", help="export the journal as JSON lines")
    p.add_argument("path")
    sub.add_parser("check-models", help="verify the ML model fingerprints")
    sub.add_parser("new-secret", help="print a random 64-hex-character secret")
    sub.add_parser("test-email", help="send a test alert e-mail with the settings in .env")
    args = parser.parse_args(argv)

    if args.cmd == "new-secret":
        print(secrets.token_hex(32))
        return
    if args.cmd == "check-models":
        from hestia.ml.runtime import MLAdvisor

        status = MLAdvisor().status()
        print(json.dumps(status, indent=2))
        sys.exit(1 if status["errors"] else 0)

    settings = get_settings()
    db = Database(settings.db_path)
    journal = SafetyJournal(db, settings.journal_secret())
    users = UserStore(db)

    if args.cmd == "create-user":
        user = users.create(args.username, _ask_password(), Role.parse(args.role), args.name)
        journal.append(
            EventKind.USER_CHANGED,
            f"User '{user.username}' created from the console ({user.role.label})",
            actor="console",
        )
        print(f"Created {user.username} ({user.role.label})")
    elif args.cmd == "reset-password":
        found = users.by_name(args.username)
        if found is None:
            sys.exit("No such user")
        users.set_password(found.id, _ask_password())
        journal.append(
            EventKind.USER_CHANGED, f"Password of '{found.username}' reset from the console", actor="console"
        )
        print("Password changed; existing sessions ended")
    elif args.cmd == "verify-journal":
        v = journal.verify()
        print(
            json.dumps(
                {
                    "ok": v.ok,
                    "checked": v.checked,
                    "head_hash": v.head_hash,
                    "first_broken_id": v.first_broken_id,
                    "problem": v.problem,
                },
                indent=2,
            )
        )
        sys.exit(0 if v.ok else 1)
    elif args.cmd == "export-journal":
        # Pages come newest first; write the whole history oldest first.
        everything = []
        page = journal.entries(limit=1000)
        while page:
            everything.extend(page)
            page = journal.entries(limit=1000, before_id=page[-1].id)
        with open(args.path, "w", encoding="utf-8") as f:
            for e in reversed(everything):
                f.write(json.dumps(e.as_dict(), ensure_ascii=False) + "\n")
        print(f"Exported {len(everything)} entries to {args.path}")
    elif args.cmd == "test-email":
        import asyncio

        from hestia.runtime.notifier import AlertError, Notifier

        try:
            asyncio.run(Notifier(settings, journal).send_test("console"))
        except AlertError as exc:
            sys.exit(f"Not sent: {exc}")
        print(f"Sent to {', '.join(settings.alert_recipients)}")


if __name__ == "__main__":
    main()
