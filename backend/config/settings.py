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
    "dialer",
    "messaging",
    "payments",
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
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.IsSettled"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_RATES": {"login": "10/min", "otp": "10/min", "reveal": "30/min", "export": "6/min", "bulk": "30/min", "admin": "60/min", "dial": "60/min", "sms": "30/min", "whatsapp": "60/min", "payment": "30/min", "dialer_login": "5/min"},
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

# ---- Smartflo (dialler). Everything stays OFF until SMARTFLO_ENABLED=true, so no customer can be called by accident.
SMARTFLO_ENABLED = env_bool("SMARTFLO_ENABLED", False)
SMARTFLO_API_TOKEN = env("SMARTFLO_API_TOKEN", "")  # long-lived account token; never sent to the browser
SMARTFLO_API_BASE = env("SMARTFLO_API_BASE", "https://api-smartflo.tatateleservices.com")
SMARTFLO_TIMEOUT_SECONDS = int(env("SMARTFLO_TIMEOUT_SECONDS", "10"))
# "type" sent with click-to-call. "dialer" ties the call to the agent's running campaign session and Smartflo answers
# "No agent session details were found" without one, so it is left out by default (a plain click-to-call).
SMARTFLO_CLICK_TO_CALL_TYPE = env("SMARTFLO_CLICK_TO_CALL_TYPE", "")
# The number the customer sees (Smartflo's "caller_id", the account's outgoing number) and how long Smartflo waits for the agent, in seconds.
SMARTFLO_CALLER_ID = env("SMARTFLO_CALLER_ID", "")
SMARTFLO_CALL_TIMEOUT = int(env("SMARTFLO_CALL_TIMEOUT", "300"))
# Ends a live call from the agent screen. NOT yet checked against Smartflo's documentation: if Smartflo refuses it the agent
# is told to end the call on the phone, and the path or body can be corrected here without a code change.
SMARTFLO_HANGUP_PATH = env("SMARTFLO_HANGUP_PATH", "/v1/call/hangup")
# Saves a call outcome (the "Disposition Detail Update" page of Smartflo's API reference shows this URL). The CRM used the older
# /v1/dialer/disposition_details_update from the vendor's Postman collection; if Smartflo answers 404 the old path is tried once.
SMARTFLO_DISPOSITION_PATH = env("SMARTFLO_DISPOSITION_PATH", "/v1/dialer/store-disposition")

# ---- MSG91
MSG91_AUTHKEY = env("MSG91_AUTHKEY", "")
MSG91_OTP_TEMPLATE_ID = env("MSG91_OTP_TEMPLATE_ID", "")
# "flow" = SMS Flow API (templates made under SMS > Templates, with named variables such as ##OTP##).
# "otp"  = the separate Send OTP API (templates made in MSG91's OTP section).
MSG91_API = env("MSG91_API", "flow")
MSG91_FLOW_URL = env("MSG91_FLOW_URL", "https://control.msg91.com/api/v5/flow")
MSG91_OTP_URL = env("MSG91_OTP_URL", "https://control.msg91.com/api/v5/otp")
# Name of the code variable in the approved template (##OTP## in template OTP-1 v1.0).
MSG91_OTP_VARIABLE = env("MSG91_OTP_VARIABLE", "OTP")
# Collections SMS (reminder and payment-link templates). OFF until switched on, so nothing reaches a borrower by accident.
MSG91_SMS_ENABLED = env_bool("MSG91_SMS_ENABLED", False)
# Used as the ##url## of the payment-link templates until the Easebuzz link is wired. Must be https://. Empty = a link must be given.
PAYMENT_LINK_DEFAULT = env("PAYMENT_LINK_DEFAULT", "")
# Most SMS one loan may get in a day (all templates together). Sending the same template again needs a confirmation first.
SMS_MAX_PER_LOAN_PER_DAY = int(env("SMS_MAX_PER_LOAN_PER_DAY", "3"))

# ---- Easebuzz EasyCollect payment links. OFF until EASEBUZZ_ENABLED=true and the key and salt are set
# (deploy/server/set-easebuzz.sh). The sub-merchant id is only for sub-aggregator merchants and may stay empty. The base is the SANDBOX until it is set to https://dashboard.easebuzz.in for production.
EASEBUZZ_ENABLED = env_bool("EASEBUZZ_ENABLED", False)
EASEBUZZ_KEY = env("EASEBUZZ_KEY", "")
EASEBUZZ_SALT = env("EASEBUZZ_SALT", "")
EASEBUZZ_SUB_MERCHANT_ID = env("EASEBUZZ_SUB_MERCHANT_ID", "")
EASEBUZZ_BASE_URL = env("EASEBUZZ_BASE_URL", "https://testdashboard.easebuzz.in").rstrip("/")
EASEBUZZ_WEBHOOK_TOKEN = env("EASEBUZZ_WEBHOOK_TOKEN", "")     # secret in the webhook address Easebuzz calls; empty = the webhook refuses everything
PAYMENT_LINK_VALID_DAYS = int(env("PAYMENT_LINK_VALID_DAYS", "2"))    # a link made today works until the end of today + this many days

# ---- WhatsApp through MSG91. OFF until MSG91_WHATSAPP_ENABLED=true. The two URLs are from memory of MSG91's API and have not been
# checked against its documentation: if MSG91 refuses a request, correct them here without a code change.
MSG91_WHATSAPP_ENABLED = env_bool("MSG91_WHATSAPP_ENABLED", False)
MSG91_WHATSAPP_NUMBER = env("MSG91_WHATSAPP_NUMBER", "")      # the business number connected to MSG91, digits with country code
MSG91_WHATSAPP_TEMPLATE_URL = env("MSG91_WHATSAPP_TEMPLATE_URL", "https://control.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/")
MSG91_WHATSAPP_TEXT_URL = env("MSG91_WHATSAPP_TEXT_URL", "https://control.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/")
WHATSAPP_WEBHOOK_TOKEN = env("WHATSAPP_WEBHOOK_TOKEN", "")    # secret in the webhook address MSG91 calls; empty = webhook refuses everything
# WhatsApp call button: opens the agent's own WhatsApp (wa.me link) on the customer's chat so they can tap the call icon. Off until true.
WHATSAPP_CALL_ENABLED = env_bool("WHATSAPP_CALL_ENABLED", False)
# MSG91 "WhatsApp Voice Call" doc: turns calling on/off for the business number (settings only, it cannot place a call). Used by `manage.py whatsapp_calling`.
MSG91_WHATSAPP_CALL_SETTINGS_URL = env("MSG91_WHATSAPP_CALL_SETTINGS_URL", "https://control.msg91.com/api/v5/whatsapp/integrated-number/call-settings/")
WHATSAPP_SESSION_HOURS = int(env("WHATSAPP_SESSION_HOURS", "24"))   # WhatsApp lets us send free text for 24 h after the customer last wrote
WHATSAPP_MAX_PER_LOAN_PER_DAY = int(env("WHATSAPP_MAX_PER_LOAN_PER_DAY", "3"))   # template messages only

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
