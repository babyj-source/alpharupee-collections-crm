"""User administration (Admin role) and the signed-in user's own password change."""
import secrets

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Role, User, audit
from .permissions import IsAdmin
from .roles import ROLES

_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike characters


def temp_password(user=None):
    """Random one-time password that also passes the normal password rules."""
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
        if not (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)):
            continue
        try:
            validate_password(pw, user)
        except DjangoValidationError:
            continue
        return pw


def clean_mobile(value):
    """Digits with country code: 10 digits get 91 in front."""
    d = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10:
        d = "91" + d
    if len(d) != 12 or not d.startswith("91") or d[2] not in "6789":
        raise serializers.ValidationError("Enter a 10-digit Indian mobile number.")
    return d


def payload(u):
    return {
        "id": u.pk, "email": u.email, "name": u.name, "role": u.role, "role_label": u.get_role_display(),
        "mobile": u.masked_mobile, "is_active": u.is_active, "locked": u.is_locked,
        "must_change_password": u.must_change_password,
        "last_login": u.last_login, "created_at": u.created_at,
    }


class UserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    name = serializers.CharField(max_length=120)
    mobile = serializers.CharField(max_length=20)
    role = serializers.ChoiceField(choices=[r.value for r in Role])

    def validate_email(self, v):
        v = v.strip().lower()
        if User.objects.filter(email=v).exists():
            raise serializers.ValidationError("A user with this e-mail already exists.")
        return v

    def validate_name(self, v):
        v = " ".join(v.split())
        if not v:
            raise serializers.ValidationError("Enter a name.")
        return v

    def validate_mobile(self, v):
        return clean_mobile(v)


class UserUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    mobile = serializers.CharField(max_length=20, required=False)
    role = serializers.ChoiceField(choices=[r.value for r in Role], required=False)
    is_active = serializers.BooleanField(required=False)

    def validate_name(self, v):
        v = " ".join(v.split())
        if not v:
            raise serializers.ValidationError("Enter a name.")
        return v

    def validate_mobile(self, v):
        return clean_mobile(v)


class AdminBase(APIView):
    permission_classes = [IsAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin"


class UserListView(AdminBase):
    def get(self, request):
        return Response([payload(u) for u in User.objects.order_by("role", "name")])

    def post(self, request):
        ser = UserCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        pw = temp_password()
        u = User.objects.create_user(d["email"], pw, name=d["name"], mobile=d["mobile"], role=d["role"], must_change_password=True)
        audit(request, "user_created", user=request.user, object_type="user", object_id=u.pk, detail=u.role)
        resp = Response({"user": payload(u), "temp_password": pw}, status=status.HTTP_201_CREATED)
        resp["Cache-Control"] = "no-store"
        return resp


class UserDetailView(AdminBase):
    def patch(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        ser = UserUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if u.pk == request.user.pk and (d.get("role", u.role) != u.role or d.get("is_active", u.is_active) is False):
            return Response({"detail": "You cannot change your own role or deactivate yourself."}, status=status.HTTP_400_BAD_REQUEST)
        changed = []
        for k in ("name", "mobile", "role", "is_active"):
            if k in d and d[k] != getattr(u, k):
                setattr(u, k, d[k])
                changed.append(k)
        if changed:
            u.save(update_fields=changed)
            audit(request, "user_updated", user=request.user, object_type="user", object_id=u.pk, detail=",".join(changed))
        return Response({"user": payload(u)})


class UserResetPasswordView(AdminBase):
    def post(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        pw = temp_password(u)
        u.set_password(pw)
        u.must_change_password = True
        u.failed_logins, u.locked_until = 0, None
        u.save(update_fields=["password", "must_change_password", "failed_logins", "locked_until"])
        audit(request, "password_reset", user=request.user, object_type="user", object_id=u.pk)
        resp = Response({"user": payload(u), "temp_password": pw})
        resp["Cache-Control"] = "no-store"
        return resp


class UserUnlockView(AdminBase):
    def post(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        u.failed_logins, u.locked_until = 0, None
        u.save(update_fields=["failed_logins", "locked_until"])
        audit(request, "user_unlocked", user=request.user, object_type="user", object_id=u.pk)
        return Response({"user": payload(u)})


class RolesView(APIView):
    def get(self, request):
        return Response(ROLES)


class ChangePasswordView(APIView):
    """Open to a user who must change the password (that is the only thing they may do)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        u = request.user
        current = request.data.get("current_password") or ""
        new = request.data.get("new_password") or ""
        if not u.check_password(current):
            audit(request, "password_change_failed", user=u)
            return Response({"detail": "Current password is wrong."}, status=status.HTTP_400_BAD_REQUEST)
        if new == current:
            return Response({"detail": "Choose a different password."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(new, u)
        except DjangoValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        u.set_password(new)
        u.must_change_password = False
        u.save(update_fields=["password", "must_change_password"])
        update_session_auth_hash(request, u)  # keeps this session signed in
        audit(request, "password_changed", user=u)
        return Response({"user": {"id": u.pk, "email": u.email, "name": u.name, "role": u.role, "must_change_password": False}})
