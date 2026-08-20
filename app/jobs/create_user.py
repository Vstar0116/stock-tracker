"""Create a login user. No self-registration endpoint exists -- this CLI,
run by whoever administers the app, is the only way to add one.

Run with: python -m app.jobs.create_user --email a@b.com --name "A B"
Password is prompted interactively (with confirmation) so it never lands in
shell history or a process listing.
"""

import argparse
import getpass

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import User
from app.security import hash_password


def create_user(email: str, name: str, password: str) -> User:
    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"a user with email {email!r} already exists (id={existing.id})")
        user = User(email=email, name=name, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _prompt_password() -> str:
    while True:
        password = getpass.getpass("Password: ")
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            continue
        if password != getpass.getpass("Confirm password: "):
            print("Passwords didn't match, try again.")
            continue
        return password


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Create a login user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    user = create_user(args.email, args.name, _prompt_password())
    print(f"created user {user.id}: {user.email}")


if __name__ == "__main__":
    main()
