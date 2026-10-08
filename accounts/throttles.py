"""Limits login attempts per client address, so passwords can't be guessed quickly."""
from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    scope = "login"

    def get_rate(self):
        # read at request time so tests and .env changes take effect
        return getattr(settings, "LOGIN_THROTTLE_RATE", "10/min")

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
