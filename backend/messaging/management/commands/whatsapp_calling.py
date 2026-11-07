from django.core.management.base import BaseCommand, CommandError

from messaging import msg91

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


class Command(BaseCommand):
    help = ("Switch WhatsApp calling on or off for the business number in MSG91 (the 'WhatsApp Voice Call' settings API). "
            "This lets customers call the business on WhatsApp. It does not place calls.")

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["enable", "disable"])
        parser.add_argument("--hours", action="store_true", help="also limit calls to 08:00-19:00 India time, every day")

    def handle(self, *args, action, hours=False, **opts):
        call_hours = None
        if action == "enable" and hours:
            call_hours = {"status": "ENABLED", "timezone_id": "Asia/Kolkata",
                          "weekly_operating_hours": [{"day_of_week": d, "open_time": "0800", "close_time": "1900"} for d in DAYS],
                          "holiday_schedule": []}
        try:
            code, message = msg91.set_whatsapp_call_settings(action == "enable", call_hours)
        except msg91.Msg91Error as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"MSG91 replied HTTP {code}: {message or '(no message)'}")
        if code != 200:
            raise CommandError("MSG91 did not accept the call settings. Send the line above to MSG91 support or fix it in the MSG91 panel.")
