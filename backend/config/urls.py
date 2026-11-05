from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    """Used by the deploy health check: 200 only if Django can reach its database."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "database unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/health", health),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("accounts.api_urls")),
    path("api/", include("loans.urls")),
]
