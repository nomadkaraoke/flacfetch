"""Helper for enumerating registered route paths across FastAPI versions.

FastAPI <0.129 flattened included routers into ``app.routes`` as ``APIRoute``
objects (each with ``.path``). Newer FastAPI instead leaves an
``_IncludedRouter`` placeholder whose sub-routes live on ``.original_router``
and are NOT reachable via ``.path``. This walks both representations so route
assertions are version-agnostic.
"""


def collect_route_paths(app) -> set:
    """Return the set of registered route paths for a FastAPI app."""
    paths: set = set()

    def walk(routes):
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
            # Newer FastAPI: included routers expose their real routes here.
            original = getattr(route, "original_router", None)
            if original is not None and hasattr(original, "routes"):
                walk(original.routes)
            # Older FastAPI / nested routers.
            nested = getattr(route, "routes", None)
            if nested:
                walk(nested)

    walk(app.routes)
    return paths
