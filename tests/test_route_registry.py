from collections import Counter

from main import app

REQUIRED_ROUTES = {
    ("GET", "/api/chat/models"),
    ("GET", "/api/graph"),
    ("GET", "/api/search"),
    ("GET", "/api/courses/{course_id}/retrieval"),
    ("GET", "/api/courses/{course_id}/generation-settings"),
    ("PUT", "/api/courses/{course_id}/generation-settings"),
    ("POST", "/api/courses/{course_id}/generation-runs"),
    ("POST", "/api/generation-runs/{run_id}/retry"),
    ("GET", "/api/courses/{course_id}/structure"),
    ("GET", "/api/modules/{module_id}"),
    ("PUT", "/api/courses/{course_id}/modules/order"),
    ("PUT", "/api/modules/{module_id}/lessons/order"),
    ("PUT", "/api/modules/{module_id}/tests/order"),
    ("POST", "/api/courses/{course_id}/tests/"),
    ("POST", "/api/courses/{course_id}/publish"),
    ("GET", "/api/versions/test/{test_id}"),
    ("GET", "/api/agent/improve-theory"),
    ("POST", "/api/feedback/"),
}


def _registered_method_paths():
    """Flatten FastAPI's included routers without relying on OpenAPI deduping."""
    def walk(router, prefix=""):
        for route in router.routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from walk(nested, prefix + route.include_context.prefix)
            elif getattr(route, "path", None) is not None:
                for method in getattr(route, "methods", set()):
                    yield method, prefix + route.path
    yield from walk(app.router)


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
