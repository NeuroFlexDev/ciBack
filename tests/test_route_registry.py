from collections import Counter

import pytest

from main import app


KNOWN_DUPLICATE = ("POST", "/api/courses/{course_id}/generate_lesson_content")


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
    assert duplicates - {KNOWN_DUPLICATE} == set()


@pytest.mark.xfail(
    strict=True,
    reason="Known duplicate handler in course_generator.py and upload.py",
)
def test_known_generate_lesson_route_is_unique():
    counts = Counter(_registered_method_paths())
    assert counts[KNOWN_DUPLICATE] == 1
