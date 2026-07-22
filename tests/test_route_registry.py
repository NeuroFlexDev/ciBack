from collections import Counter

from main import app

REQUIRED_ROUTES = {
    ("GET", "/api/chat/models"),
    ("GET", "/api/graph"),
    ("GET", "/api/search"),
    ("GET", "/api/agent/improve-theory"),
    ("POST", "/api/feedback/"),
}


def _registered_method_paths():
    """Flatten FastAPI's included routers without relying on OpenAPI deduping."""
    for mounted in app.routes:
        included_router = getattr(mounted, "original_router", None)
        if included_router is not None:
            prefix = mounted.include_context.prefix
            routes = included_router.routes
        else:
            prefix = ""
            routes = [mounted]

        for route in routes:
            path = getattr(route, "path", None)
            if path is None:
                continue
            for method in getattr(route, "methods", set()):
                yield method, f"{prefix}{path}"


def test_route_registry_has_no_unexpected_duplicates():
    counts = Counter(_registered_method_paths())
    duplicates = {key for key, count in counts.items() if count > 1}
    assert duplicates == set()


def test_required_routes_are_registered():
    assert REQUIRED_ROUTES <= set(_registered_method_paths())


def test_openapi_operation_ids_are_unique():
    operation_ids = [
        operation["operationId"]
        for path_item in app.openapi()["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
