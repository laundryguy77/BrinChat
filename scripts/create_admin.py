#!/usr/bin/env python3
"""Create an admin user for BrinChat.

Usage:
    python scripts/create_admin.py [username]

If username is not provided, prompts interactively.
Password is always entered interactively for security.
"""
import sys
import getpass
import re
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.database import get_database
from app.services.auth_service import get_auth_service
from app.models.auth_schemas import UserCreate


def validate_password(password: str) -> tuple[bool, str]:
    """Validate password meets security requirements.

    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\\;'`~]", password):
        return False, "Password must contain at least one special character"
    return True, ""


def create_admin(username: str, password: str) -> bool:
    """Create an admin user.

    Args:
        username: The admin username
        password: The admin password

    Returns:
        True if created successfully, False otherwise
    """
    auth_service = get_auth_service()
    db = get_database()

    # Check if user exists
    existing = db.fetchone("SELECT id FROM users WHERE username = ?", (username,))
    if existing:
        print(f"Error: Username '{username}' already exists")

        # Check if already admin
        is_admin = db.fetchone("SELECT is_admin FROM users WHERE username = ?", (username,))
        if is_admin and is_admin["is_admin"]:
            print(f"User '{username}' is already an admin")
            return False

        # Offer to promote existing user
        response = input(f"Would you like to promote '{username}' to admin? [y/N]: ")
        if response.lower() == 'y':
            db.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
            print(f"User '{username}' has been promoted to admin")
            return True
        return False

    try:
        # Create user
        user_data = UserCreate(username=username, password=password)
        result = auth_service.create_user(user_data)

        if not result:
            print(f"Error: Failed to create user '{username}'")
            return False

        # Promote to admin
        db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (result.id,))
        print(f"Admin user '{username}' created successfully (ID: {result.id})")
        return True

    except ValueError as e:
        print(f"Error: {e}")
        return False
    except Exception as e:
        print(f"Error creating admin: {e}")
        return False


def main():
    """Main entry point."""
    print("=" * 50)
    print("BrinChat Admin User Creation")
    print("=" * 50)
    print()

    # Get username
    if len(sys.argv) > 1:
        username = sys.argv[1]
    else:
        username = input("Enter admin username: ").strip()

    if not username:
        print("Error: Username is required")
        sys.exit(1)

    if len(username) < 3:
        print("Error: Username must be at least 3 characters")
        sys.exit(1)

    if len(username) > 50:
        print("Error: Username must be at most 50 characters")
        sys.exit(1)

    # Get password
    print("\nPassword requirements:")
    print("  - At least 12 characters")
    print("  - At least one uppercase letter")
    print("  - At least one lowercase letter")
    print("  - At least one digit")
    print("  - At least one special character")
    print()

    while True:
        password = getpass.getpass("Enter password: ")

        valid, error = validate_password(password)
        if not valid:
            print(f"Error: {error}")
            continue

        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords do not match")
            continue

        break

    print()
    success = create_admin(username, password)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
