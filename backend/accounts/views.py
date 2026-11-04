from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import otp
from .models import User, audit

PENDING_KEY = "pending_user_id"
PENDING_AT = "pending_at"
_DUMMY_HASH = None


def _burn_time():
    """Spend the same time on unknown e-mails as on known ones."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = make_password("not-a-real-password")
    check_password("x", _DUMMY_HASH)


def user_payload(user):
    return {"id": user.pk, "email": user.email, "name": user.name, "role": user.role}


class CsrfView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        get_token(request)  # sets the csrftoken cookie
        return Response({"ok": True})


class LoginView(APIView):
    """Step 1: e-mail and password. On success an SMS code is sent and the session waits for step 2."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        bad = Response({"detail": "Invalid e-mail or password."}, status=status.HTTP_400_BAD_REQUEST)
        if not email or not password:
            return bad
        user = User.objects.filter(email=email).first()
        if user is None or not user.is_active:
            _burn_time()
            audit(request, "login_failed", email_tried=email, detail="unknown or inactive")
            return bad
        if user.is_locked:
            audit(request, "login_blocked", user=user, detail="locked")
            return Response({"detail": "Too many attempts. Try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not authenticate(request, email=email, password=password):
            user.failed_logins += 1
            if user.failed_logins >= settings.LOGIN_MAX_FAILURES:
                user.failed_logins = 0
                user.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
                audit(request, "account_locked", user=user)
            user.save(update_fields=["failed_logins", "locked_until"])
            audit(request, "login_failed", user=user, detail="bad password")
            return bad
        return _start_otp(request, user)


def _start_otp(request, user):
    try:
        otp.issue(user)
    except otp.OtpRateLimited:
        return Response({"detail": "Too many codes requested. Try again in an hour."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    except otp.OtpDeliveryError:
        audit(request, "otp_send_failed", user=user)
        return Response({"detail": "Could not send the code. Contact the administrator."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    request.session.flush()  # fresh session for the pending login
    request.session[PENDING_KEY] = user.pk
    request.session[PENDING_AT] = timezone.now().timestamp()
    audit(request, "otp_sent", user=user)
    return Response({"otp_required": True, "mobile_hint": user.masked_mobile, "expires_in": settings.OTP_TTL_SECONDS})


def _pending_user(request):
    uid = request.session.get(PENDING_KEY)
    at = request.session.get(PENDING_AT)
    if not uid or not at:
        return None
    if timezone.now().timestamp() - at > 15 * 60:
        return None
    return User.objects.filter(pk=uid, is_active=True).first()


class VerifyView(APIView):
    """Step 2: the SMS code. Only now is the user logged in."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        user = _pending_user(request)
        if user is None:
            return Response({"detail": "Sign in again."}, status=status.HTTP_400_BAD_REQUEST)
        if user.is_locked:
            return Response({"detail": "Too many attempts. Try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not otp.verify(user, str(request.data.get("code") or "")):
            audit(request, "otp_failed", user=user)
            return Response({"detail": "Wrong or expired code."}, status=status.HTTP_400_BAD_REQUEST)
        user.failed_logins = 0
        user.save(update_fields=["failed_logins"])
        for k in (PENDING_KEY, PENDING_AT):
            request.session.pop(k, None)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        audit(request, "login_ok", user=user)
        return Response({"user": user_payload(user)})


class ResendView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        user = _pending_user(request)
        if user is None:
            return Response({"detail": "Sign in again."}, status=status.HTTP_400_BAD_REQUEST)
        return _start_otp(request, user)


class LogoutView(APIView):
    def post(self, request):
        audit(request, "logout", user=request.user)
        logout(request)
        return Response({"ok": True})


class MeView(APIView):
    def get(self, request):
        return Response({"user": user_payload(request.user)})
