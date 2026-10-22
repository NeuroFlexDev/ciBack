"""Request-local trust boundary shared by HTTP authentication and MFA."""
from contextvars import ContextVar

surface = ContextVar("lernium_surface", default="platform")


class SurfaceMiddleware:
    def __init__(self, app, name):
        self.app, self.name = app, name

    async def __call__(self, scope, receive, send):
        token = surface.set(self.name)
        try:
            await self.app(scope, receive, send)
        finally:
            surface.reset(token)
