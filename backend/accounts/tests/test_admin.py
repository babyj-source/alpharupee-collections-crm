from django.contrib.auth import authenticate
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, User

PW = "S3cure-passw0rd!"


def client(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


class AdminBase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user("admin@example.com", PW, name="Admin", mobile="919876500001", role="admin")
        self.head = User.objects.create_user("head@example.com", PW, name="Head", mobile="919876543210", role="head")
        self.agent = User.objects.create_user("agent@example.com", PW, name="Agent", mobile="919876543211", role="agent")


class UserAdminTests(AdminBase):
    def test_only_admin_can_manage_users(self):
        for u in (self.head, self.agent):
            self.assertEqual(client(u).get("/api/users").status_code, 403)
            self.assertEqual(client(u).post("/api/users", {}, format="json").status_code, 403)
        self.assertEqual(client(self.admin).get("/api/users").status_code, 200)
        self.assertEqual(client().get("/api/users").status_code, 403)

    def test_admin_has_head_access_too(self):
        self.assertEqual(client(self.admin).get("/api/dashboard").status_code, 200)
        self.assertEqual(client(self.admin).get("/api/agents").status_code, 200)

    def test_create_user_gives_a_one_time_password(self):
        r = client(self.admin).post("/api/users", {"email": "New.Agent@Example.com", "name": "New  Agent", "mobile": "98765 43299",
                                                   "role": "agent"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r["Cache-Control"], "no-store")
        body = r.json()
        u = User.objects.get(email="new.agent@example.com")
        self.assertEqual((u.name, u.mobile, u.role, u.must_change_password), ("New Agent", "919876543299", "agent", True))
        self.assertEqual(body["user"]["mobile"], "XXXXXXXX3299")  # masked
        self.assertIsNotNone(authenticate(None, email=u.email, password=body["temp_password"]))
        self.assertNotIn(body["temp_password"], " ".join(AuditLog.objects.values_list("detail", flat=True)))
        self.assertTrue(AuditLog.objects.filter(action="user_created", object_id=str(u.pk)).exists())
        self.assertNotIn("temp_password", client(self.admin).get("/api/users").content.decode())

    def test_create_validation(self):
        c = client(self.admin)
        ok = {"email": "x@example.com", "name": "X", "mobile": "9876543210", "role": "agent"}
        self.assertEqual(c.post("/api/users", {**ok, "email": "AGENT@example.com"}, format="json").status_code, 400)  # exists
        self.assertEqual(c.post("/api/users", {**ok, "mobile": "12345"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/users", {**ok, "mobile": "5876543210"}, format="json").status_code, 400)  # not a mobile
        self.assertEqual(c.post("/api/users", {**ok, "role": "boss"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/users", {**ok, "name": "  "}, format="json").status_code, 400)
        self.assertEqual(User.objects.count(), 3)

    def test_edit_role_and_deactivate(self):
        c = client(self.admin)
        r = c.patch(f"/api/users/{self.agent.pk}", {"role": "head", "name": "Agent Renamed"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual((self.agent.role, self.agent.name), ("head", "Agent Renamed"))
        c.patch(f"/api/users/{self.agent.pk}", {"is_active": False}, format="json")
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.is_active)
        login = client().post("/api/auth/login", {"email": "agent@example.com", "password": PW}, format="json")
        self.assertEqual(login.status_code, 400)

    def test_admin_cannot_lock_themselves_out(self):
        c = client(self.admin)
        self.assertEqual(c.patch(f"/api/users/{self.admin.pk}", {"role": "agent"}, format="json").status_code, 400)
        self.assertEqual(c.patch(f"/api/users/{self.admin.pk}", {"is_active": False}, format="json").status_code, 400)
        self.assertEqual(c.patch(f"/api/users/{self.admin.pk}", {"name": "Renamed"}, format="json").status_code, 200)

    def test_reset_password_and_unlock(self):
        from django.utils import timezone
        User.objects.filter(pk=self.agent.pk).update(failed_logins=3, locked_until=timezone.now() + timezone.timedelta(minutes=10))
        r = client(self.admin).post(f"/api/users/{self.agent.pk}/reset-password")
        self.assertEqual(r.status_code, 200)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.must_change_password)
        self.assertFalse(self.agent.is_locked)
        self.assertIsNone(authenticate(None, email=self.agent.email, password=PW))
        self.assertIsNotNone(authenticate(None, email=self.agent.email, password=r.json()["temp_password"]))
        User.objects.filter(pk=self.head.pk).update(locked_until=timezone.now() + timezone.timedelta(minutes=10))
        client(self.admin).post(f"/api/users/{self.head.pk}/unlock")
        self.head.refresh_from_db()
        self.assertFalse(self.head.is_locked)

    def test_roles_matrix(self):
        r = client(self.agent).get("/api/roles")
        self.assertEqual([x["role"] for x in r.json()], ["admin", "head", "agent"])


class PasswordChangeTests(AdminBase):
    def setUp(self):
        super().setUp()
        User.objects.filter(pk=self.agent.pk).update(must_change_password=True)
        self.agent.refresh_from_db()

    def test_forced_change_blocks_everything_else(self):
        c = client(self.agent)
        self.assertEqual(c.get("/api/loans").status_code, 403)
        self.assertEqual(c.get("/api/dashboard").status_code, 403)
        me = c.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["user"]["must_change_password"])
        self.assertEqual(c.post("/api/auth/logout").status_code, 200)

    def test_change_password(self):
        c = client(self.agent)
        bad = c.post("/api/auth/password", {"current_password": "wrong", "new_password": "An0ther-long-one"}, format="json")
        self.assertEqual(bad.status_code, 400)
        weak = c.post("/api/auth/password", {"current_password": PW, "new_password": "12345678"}, format="json")
        self.assertEqual(weak.status_code, 400)
        same = c.post("/api/auth/password", {"current_password": PW, "new_password": PW}, format="json")
        self.assertEqual(same.status_code, 400)
        ok = c.post("/api/auth/password", {"current_password": PW, "new_password": "An0ther-long-one"}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.must_change_password)
        self.assertIsNotNone(authenticate(None, email=self.agent.email, password="An0ther-long-one"))
        self.assertEqual(client(self.agent).get("/api/loans").status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="password_changed").exists())


class CreateCommandTests(TestCase):
    def test_must_change_flag_and_admin_role(self):
        import os
        from django.core.management import call_command
        os.environ["TEST_PW"] = "Str0ng-start-pass"
        self.addCleanup(os.environ.pop, "TEST_PW", None)
        call_command("create_crm_user", "boss@example.com", "--name", "Boss", "--mobile", "919876500001", "--role", "admin",
                     "--must-change", "--password-env", "TEST_PW", stdout=__import__("io").StringIO())
        u = User.objects.get(email="boss@example.com")
        self.assertEqual((u.role, u.must_change_password, u.is_head, u.is_admin), ("admin", True, True, True))
