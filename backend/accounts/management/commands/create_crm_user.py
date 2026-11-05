import getpass
import os

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password

from accounts.models import Role, User


class Command(BaseCommand):
    help = "Create (or with --update, change) a CRM user. The password is typed with hidden input."

    def add_arguments(self, p):
        p.add_argument("email")
        p.add_argument("--name", required=True)
        p.add_argument("--mobile", required=True, help="digits with country code, e.g. 919876543210")
        p.add_argument("--role", choices=[r.value for r in Role], default=Role.AGENT)
        p.add_argument("--update", action="store_true", help="change an existing user (asks for a new password)")
        p.add_argument("--must-change", action="store_true", help="make the user choose their own password at first sign-in")
        p.add_argument("--password-env", help="read the password from this environment variable instead of asking")

    def handle(self, *a, **o):
        email = o["email"].strip().lower()
        mobile = "".join(ch for ch in o["mobile"] if ch.isdigit())
        if len(mobile) < 10:
            raise CommandError("mobile must have at least 10 digits")
        existing = User.objects.filter(email=email).first()
        if existing and not o["update"]:
            raise CommandError("user exists; use --update")
        if o["password_env"]:
            pw = os.environ.get(o["password_env"], "")
        else:
            pw = getpass.getpass("Password: ")
            if pw != getpass.getpass("Repeat password: "):
                raise CommandError("passwords differ")
        try:
            validate_password(pw)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        if existing:
            existing.name, existing.mobile, existing.role = o["name"], mobile, o["role"]
            existing.failed_logins, existing.locked_until, existing.is_active = 0, None, True
            existing.must_change_password = bool(o["must_change"])
            existing.set_password(pw)
            existing.save()
            self.stdout.write(f"Updated {email} ({existing.role})")
        else:
            u = User.objects.create_user(email, pw, name=o["name"], mobile=mobile, role=o["role"],
                                         must_change_password=bool(o["must_change"]))
            self.stdout.write(f"Created {email} ({u.role})")
