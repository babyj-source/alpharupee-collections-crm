"""Settings. Everything secret comes from environment variables (see deploy/server/setup-env.sh)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)
USE_SQLITE = env_bool("DJANGO_USE_SQLITE", False)  # tests and local work only

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or USE_SQLITE:
        SECRET_KEY = "dev-only-insecure-key-not-for-servers"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is not set")

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]
# The deploy health check calls http://127.0.0.1/api/health on the server itself.
ALLOWED_HOSTS += [h for h in ("127.0.0.1", "localhost") if h not in ALLOWED_HOSTS]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "loans",
]
# The Django admin is deliberately NOT installed: it signs in with a password only, which would
# bypass the OTP step. Users are created with `manage.py create_crm_user`.

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "accounts.User"
TEMPLATES = []

if USE_SQLITE:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
            "NAME": env("DB_NAME", "crm"),
            "USER": env("DB_USER", "crm_app"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "OPTIONS": {"sslmode": env("DB_SSLMODE", "require")},
            "CONN_MAX_AGE": 60,
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---- sessions, cookies, transport
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not (DEBUG or USE_SQLITE)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 0 if (DEBUG or USE_SQLITE) else 86400  # raise to a year once HTTPS is settled
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# ---- API
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_RATES": {"login": "10/min", "otp": "10/min", "reveal": "30/min"},
    "NUM_PROXIES": int(env("NUM_PROXIES", "1")),  # nginx. Use 2 when Cloudflare is switched on.
    "DEFAULT_PAGINATION_CLASS": "loans.pagination.Page",
    "PAGE_SIZE": 50,
}

# ---- login security
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 15
OTP_TTL_SECONDS = 600  # the SMS template says "Valid for 10 minutes"
OTP_MAX_ATTEMPTS = 5
OTP_MAX_SENDS_PER_HOUR = 5
# Prints the OTP to the server log instead of sending it. Never turn this on in production.
OTP_DEV_MODE = env_bool("OTP_DEV_MODE", False)

# ---- MSG91
MSG91_AUTHKEY = env("MSG91_AUTHKEY", "")
MSG91_OTP_TEMPLATE_ID = env("MSG91_OTP_TEMPLATE_ID", "")
MSG91_OTP_URL = env("MSG91_OTP_URL", "https://control.msg91.com/api/v5/otp")
# Name of the code variable in the approved template (##number## in template OTP-1).
MSG91_OTP_VARIABLE = env("MSG91_OTP_VARIABLE", "number")

# ---- LMS (read-only MySQL)
LMS_DB = {
    "host": env("LMS_DB_HOST", ""),
    "port": int(env("LMS_DB_PORT", "3306") or 3306),
    "user": env("LMS_DB_USER", ""),
    "password": env("LMS_DB_PASSWORD", ""),
    "database": env("LMS_DB_NAME", ""),
    "ssl_ca": env("LMS_DB_SSL_CA", ""),  # optional CA file; without it the link is encrypted but not verified
}
LMS_TENANT_CODE = env("LMS_TENANT_CODE", "NBFC1")
LMS_SCHEMA = env("LMS_SCHEMA", "encorebootdb")
LMS_QUERY_TIMEOUT_MS = int(env("LMS_QUERY_TIMEOUT_MS", "30000"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
