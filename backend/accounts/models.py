from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    HEAD = "head", "Collections head"
    AGENT = "agent", "Collections agent"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password, **extra):
        if not email:
            raise ValueError("email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    """Login is the e-mail address. No Django admin and no permission mixin on purpose."""

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=15, help_text="Digits only with country code, e.g. 919876543210")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.AGENT)
    is_active = models.BooleanField(default=True)
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name", "mobile"]

    def __str__(self):
        return self.email

    @property
    def is_head(self):
        return self.role == Role.HEAD

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def masked_mobile(self):
        return mask_mobile(self.mobile)


def mask_mobile(mobile):
    digits = "".join(ch for ch in (mobile or "") if ch.isdigit())
    if len(digits) < 4:
        return ""
    return "X" * (len(digits) - 4) + digits[-4:]


class OtpChallenge(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otp_challenges")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]


class AuditLog(models.Model):
    """Who did what. Never stores OTP codes, passwords or phone numbers."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    email_tried = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    detail = models.CharField(max_length=300, blank=True)


def audit(request, action, user=None, object_type="", object_id="", detail="", email_tried=""):
    ip = None
    if request is not None:
        ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or None
    AuditLog.objects.create(
        user=user if (user is not None and getattr(user, "pk", None)) else None,
        email_tried=email_tried[:254],
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        ip=ip,
        detail=detail[:300],
    )
