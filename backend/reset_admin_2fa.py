"""Reset an administrator's authenticator enrollment from a trusted server shell."""
import sys

from sqlalchemy import select

from app import SessionLocal, User


def main() -> int:
    email = input("Administrator email: ").strip().lower()
    confirmation = input("Type RESET to clear this administrator's authenticator enrollment: ").strip()
    if confirmation != "RESET":
        print("No changes made.", file=sys.stderr)
        return 1
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email, User.role == "admin"))
        if not user:
            print("Administrator account not found.", file=sys.stderr)
            return 1
        user.totp_secret_ciphertext = None
        user.totp_enabled = False
        session.commit()
    print("Authenticator enrollment cleared. The admin must set it up again at next sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
