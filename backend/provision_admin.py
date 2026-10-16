"""Securely create the first KhetX administrator from an interactive shell."""
import re
import sys
from getpass import getpass

from sqlalchemy import select

from app import SessionLocal, User, ph


def main() -> int:
    email = input("Administrator email: ").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("Enter a valid email address.", file=sys.stderr)
        return 2
    name = input("Administrator display name: ").strip()
    if not name or len(name) > 120:
        print("Enter a name between 1 and 120 characters.", file=sys.stderr)
        return 2
    password = getpass("Administrator password (16+ characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 16 or password != confirmation:
        print("Passwords must match and be at least 16 characters.", file=sys.stderr)
        return 2
    with SessionLocal() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing:
            if existing.role == "admin":
                existing.name = name
                existing.password_hash = ph.hash(password)
                session.commit()
                print("Administrator credentials rotated securely.")
                return 0
            print("That email belongs to a non-admin account. No role changes were made.", file=sys.stderr)
            return 1
        session.add(User(email=email, name=name, phone="", password_hash=ph.hash(password), role="admin"))
        session.commit()
    print("Administrator account provisioned. Sign in at /admin/login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
