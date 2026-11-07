import hmac
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import audit
from accounts.permissions import IsAdmin
from loans.pagination import Page
from loans.views import scoped

from . import easebuzz
from . import webhook
from .models import PaymentLink, PaymentWebhookLog
from .service import LinkRefused, StaleClosure, create_for_loan, preview, refresh


def _row(link):
    return {"id": link.pk, "ref": link.merchant_txn, "amount": str(link.amount), "as_on": link.closure_as_on, "url": link.payment_url, "state": link.state,
            "state_label": link.get_state_display(), "expires_on": link.expires_on, "created_at": link.created_at, "paid_at": link.paid_at,
            "by": link.created_by.name if link.created_by else "", "lms": link.lms_state}


def _loan(request, data=None):
    src = data if data is not None else request.query_params
    try:
        pk = int(src.get("loan"))
    except (TypeError, ValueError):
        return None
    return get_object_or_404(scoped(request.user, grants=True), pk=pk)


class LinkView(APIView):
    """GET: the account's recent payment links and what a new one would be for. POST: make today's link (or return the one already made)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment"

    def get(self, request):
        loan = _loan(request)
        if loan is None:
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        links = list(loan.payment_links.select_related("created_by")[:10])
        return Response({"preview": preview(loan), "links": [_row(x) for x in links]})

    def post(self, request):
        loan = _loan(request, request.data)
        if loan is None:
            return Response({"detail": "Give the loan id."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            link, created = create_for_loan(loan, request.user, confirm_stale=request.data.get("confirm_stale") is True)
        except StaleClosure as exc:
            return Response({"detail": str(exc), "stale": True}, status=status.HTTP_409_CONFLICT)
        except LinkRefused as exc:
            audit(request, "payment_link_refused", user=request.user, object_type="loan", object_id=loan.pk, detail=str(exc)[:200])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if created:
            audit(request, "payment_link_created", user=request.user, object_type="loan", object_id=loan.pk, detail=f"{link.merchant_txn} {link.amount}")
        return Response(_row(link), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class LinkRefreshView(APIView):
    """Ask Easebuzz whether this link has been paid."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment"

    def post(self, request, pk):
        link = get_object_or_404(PaymentLink.objects.select_related("created_by", "loan").filter(loan__in=scoped(request.user, grants=True).values("pk")), pk=pk)
        try:
            if refresh(link):
                audit(request, "payment_link_" + link.state, user=request.user, object_type="loan", object_id=link.loan_id, detail=link.merchant_txn)
        except easebuzz.EasebuzzDisabled as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except easebuzz.EasebuzzError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(_row(link))


@method_decorator(csrf_exempt, name="dispatch")
class EasebuzzWebhookView(APIView):
    """Easebuzz calls this when a payment changes state. No login; a secret token in the address protects it. Always answers 200
    (Easebuzz only accepts 200) once the token is right: the payment itself is confirmed with Easebuzz, not taken from this call."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        want = settings.EASEBUZZ_WEBHOOK_TOKEN
        got = request.query_params.get("token", "")
        if not want or not hmac.compare_digest(want.encode(), got.encode()):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        data = {k: v for k, v in request.POST.items()} if request.POST else (request.data if isinstance(request.data, dict) else {})
        try:
            handled = webhook.handle(data)
        except Exception:      # an odd call must not make Easebuzz retry for hours; it is in the log
            handled = "error"
        webhook.log(data, handled)
        return Response({"ok": True})


class WebhookLogView(APIView):
    """Admin: the last webhook calls (only the fields we use), to check the real shape."""

    permission_classes = [IsAdmin]

    def get(self, request):
        return Response([{"at": w.created_at, "handled": w.handled, "body": w.body} for w in PaymentWebhookLog.objects.all()[:30]])


def _scoped_links(request):
    """Payment links on the accounts this user may see (agents: their own; head/admin: all), narrowed by ?days= and ?q=."""
    p = request.query_params
    qs = PaymentLink.objects.filter(loan__in=scoped(request.user).values("pk")).select_related("loan", "loan__assigned_agent", "created_by")
    try:
        days = int(p.get("days", "0") or 0)
    except ValueError:
        days = 0
    if days > 0:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    q = (p.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(loan__account_id__icontains=q) | Q(loan__borrower_name__icontains=q) | Q(merchant_txn__icontains=q))
    return qs


def _list_row(link):
    agent = link.loan.assigned_agent
    return {**_row(link), "loan_id": link.loan_id, "account_id": link.loan.account_id, "borrower_name": link.loan.borrower_name,
            "agent_name": agent.name if agent else "", "lms_label": link.get_lms_state_display(), "checked_at": link.checked_at}


class LinksListView(APIView):
    """Payment tracking screen: every payment link on the user's accounts, newest first. Filters: state, lms, q, days."""

    def get(self, request):
        qs = _scoped_links(request)
        state, lms = request.query_params.get("state", ""), request.query_params.get("lms", "")
        if state in {c for c, _ in PaymentLink.State.choices}:
            qs = qs.filter(state=state)
        if lms in {c for c, _ in PaymentLink.Lms.choices if c}:
            qs = qs.filter(lms_state=lms)
        elif lms == "notpaid":
            qs = qs.filter(lms_state="")
        page = Page()
        rows = page.paginate_queryset(qs, request)
        return page.get_paginated_response([_list_row(x) for x in rows])


class LinksSummaryView(APIView):
    """The tiles on the payment tracking screen: how many links are waiting, paid, expired, and where the LMS posting stands."""

    def get(self, request):
        qs = _scoped_links(request)
        def part(**f):
            a = qs.filter(**f).aggregate(n=Count("id"), amount=Sum("amount"))
            return {"count": a["n"], "amount": str(a["amount"] or 0)}
        return Response({
            "active": part(state="active"), "paid": part(state="paid"), "expired": part(state="expired"),
            "lms_pending": part(lms_state="pending"), "lms_posted": part(lms_state="posted"), "lms_failed": part(lms_state="failed"),
        })
