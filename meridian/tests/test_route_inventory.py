"""Every route sits behind ``require_http_principal``, except an explicit public list."""

from __future__ import annotations

import copy

from fastapi import APIRouter
from fastapi.routing import APIRoute

from backend import main
from backend.http_auth import require_http_principal
from backend.routers import (
    chat_router,
    diagnostics_router,
    journeys_router,
    memory_router,
    packages_router,
    products_router,
    session_router,
)
from backend.routers import products as products_module

PUBLIC_PATHS = {"/health"}
ROUTERS = [
    chat_router,
    packages_router,
    products_router,
    products_module.legacy_router,
    memory_router,
    diagnostics_router,
    journeys_router,
    session_router,
]


def depends_on_principal(dependant) -> bool:
    """True when ``require_http_principal`` appears anywhere in the dependency tree."""
    if dependant.call is require_http_principal:
        return True
    return any(depends_on_principal(child) for child in dependant.dependencies)


def api_routes(router) -> list[APIRoute]:
    return [route for route in router.routes if isinstance(route, APIRoute)]


def open_routes(routers: list[APIRouter], app_routes: list[APIRoute]) -> list[str]:
    """Method and path of every route that is not public and lacks the dependency."""
    routes = [route for router in routers for route in api_routes(router)] + app_routes
    return sorted(
        f"{sorted(route.methods)} {route.path}"
        for route in routes
        if route.path not in PUBLIC_PATHS and not depends_on_principal(route.dependant)
    )


def test_every_route_requires_the_principal_except_the_public_list():
    assert open_routes(ROUTERS, api_routes(main.app)) == []


def test_the_walk_covers_every_path_the_app_serves():
    walked = {r.path for router in ROUTERS for r in api_routes(router)}
    walked |= {r.path for r in api_routes(main.app)}
    served = set(main.app.openapi()["paths"]) | {"/openapi.json", "/docs", "/redoc"}
    assert served - walked == set()


def test_the_public_list_is_exactly_the_liveness_probe():
    app_paths = {route.path for route in api_routes(main.app)}
    public = {
        route.path
        for route in api_routes(main.app)
        if not depends_on_principal(route.dependant)
    }
    assert public == PUBLIC_PATHS
    assert PUBLIC_PATHS <= app_paths


def test_a_router_route_that_drops_the_dependency_is_reported():
    scratch = copy.copy(session_router)
    scratch.routes = list(session_router.routes)
    target = api_routes(scratch)[0]
    stripped = copy.copy(target)
    stripped.dependant = copy.copy(target.dependant)
    stripped.dependant.dependencies = [
        child
        for child in target.dependant.dependencies
        if not depends_on_principal(child)
    ]
    scratch.routes[scratch.routes.index(target)] = stripped
    found = open_routes([scratch], [])
    assert found == [f"{sorted(target.methods)} {target.path}"]


def test_a_new_router_without_the_dependency_is_reported():
    router = APIRouter(prefix="/api/new")

    @router.get("/open")
    async def open_endpoint() -> dict[str, str]:
        return {}

    assert open_routes([router], []) == ["['GET'] /api/new/open"]


def test_a_new_router_with_the_dependency_passes():
    router = APIRouter(prefix="/api/new", dependencies=[main.Depends(require_http_principal)])

    @router.get("/closed")
    async def closed_endpoint() -> dict[str, str]:
        return {}

    assert open_routes([router], []) == []
