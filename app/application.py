"""Assemble isolated platform and operator APIs from shared domain handlers."""
import os
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from app.api_catalog import app as catalog
from app.core.config import settings
from app.core.http_security import SecurityMiddleware
from app.core.upload_limits import UploadBodyLimitMiddleware
from app.core.surface import SurfaceMiddleware
from app.services.auth_service import require_admin


def operator_dependency(dependant):
    return dependant.call is require_admin or any(operator_dependency(d) for d in dependant.dependencies)


def operator_route(route, prefix=""):
    return (prefix + route.path).startswith("/api/admin/") or operator_dependency(route.dependant)


def create_app(name):
    if name not in {"platform", "admin"}:
        raise ValueError("Unknown API surface")
    if name == "admin":
        key = settings.ADMIN_JWT_SECRET.get_secret_value()
        if len(key) < 32 or key == settings.JWT_SECRET.get_secret_value():
            raise RuntimeError("The admin service requires a separate ADMIN_JWT_SECRET (32+ characters)")
    app = FastAPI(title="Lernium Administration API" if name == "admin" else "Lernium API")
    app.state.surface = name
    shared = {"/api/auth/login", "/api/auth/me", "/api/auth/logout-all", "/api/me",
              "/api/change-password", "/api/auth/password-reset", "/api/auth/password-reset/confirm",
              "/api/healthz", "/api/readiness", "/api/support/grants",
              "/api/support/grants/{grant_id}/revoke"}
    def select_routes(source, prefix=""):
        selected = APIRouter()
        for route in source.routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                context = route.include_context
                child = select_routes(nested, prefix + context.prefix)
                selected.include_router(child, prefix=context.prefix, tags=context.tags,
                    dependencies=context.dependencies, responses=context.responses,
                    default_response_class=context.default_response_class,
                    callbacks=context.callbacks, deprecated=context.deprecated,
                    include_in_schema=context.include_in_schema,
                    generate_unique_id_function=context.generate_unique_id_function)
                continue
            if not isinstance(route, APIRoute):
                continue
            path = prefix + route.path
            operator = operator_route(route, prefix)
            allowed = not operator if name == "platform" else (
                operator or path in shared or path.startswith(("/api/auth/mfa/", "/api/legal/")))
            if allowed:
                selected.routes.append(route)
        return selected
    app.include_router(select_routes(catalog.router))
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(UploadBodyLimitMiddleware)
    origins = settings.ADMIN_CORS_ORIGINS if name == "admin" else os.getenv("CORS_ORIGINS", settings.CORS_ORIGINS)
    app.add_middleware(CORSMiddleware, allow_origins=[v.strip() for v in origins.split(",") if v.strip()],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(SurfaceMiddleware, name=name)
    return app
