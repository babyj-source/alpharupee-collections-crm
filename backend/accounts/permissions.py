from rest_framework.permissions import BasePermission


def _ok(u):
    """Signed in, and not stuck on the forced password change."""
    return bool(u and u.is_authenticated and not u.must_change_password)


class IsSettled(BasePermission):
    """Default for every endpoint: a user who must change the password can do nothing else until they have."""

    def has_permission(self, request, view):
        return _ok(request.user)


class IsHead(BasePermission):
    def has_permission(self, request, view):
        return _ok(request.user) and request.user.is_head


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return _ok(request.user) and request.user.is_admin
