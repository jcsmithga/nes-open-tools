"""The FastAPI application factory. The routes are in `server/routes/`. See
docs/randomizer_devplan.md, "Routes"."""

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Mount

from golf.randomizer.catalog import REPO_ROOT
from golf.rendering.hole_renders import HoleRenders

from .auth import DiscordClient, current_user
from .builder import BuilderUnavailableError, SeedBuilder
from .config import Config
from .db import Database, DatabaseError
from .logging import request_id
from .pages import PageCatalog
from .ratelimit import GENERATE_CAPACITY, GENERATE_REFILL_SECONDS, RateLimiter
from .routes.account import account_router
from .routes.admin_pages import admin_router
from .routes.claim_pages import claim_router
from .routes.host_pages import host_router
from .routes.round_pages import round_router
from .routes.seed_pages import seed_router
from .routes.site import RANGEFINDER_DATA_URL, site_router
from .seeds import NOT_BACKFILLED, unfilled_seeds
from .static_files import (
    LOGO,
    LOGO_SIZE,
    STATIC_DIR,
    CachedStaticFiles,
    StaticVersions,
)
from .strings import Strings
from .timings import EXCEPTION, Sample, TimingSink, flush_periodically
from .version import site_version as read_site_version
from .views import calendar_date, timestamp

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"

#: the route of a request that matched nothing, which would otherwise be every 404 path
UNMATCHED = "unmatched"

#: paths a missing resource answers with JSON rather than the not-found page
MACHINE_SUFFIXES = (".json", ".ips")

SESSION_COOKIE = "golf_session"
#: seconds a sign-in lasts
SESSION_MAX_AGE = 30 * 24 * 60 * 60

log = logging.getLogger(__name__)


def route_template(request: Request) -> str:
    """The matched route's path, which is what a timing row is grouped by.

    A mounted app, such as the static files, sets no route of its own, so its requests
    are grouped under the mount's path instead.
    """
    path = getattr(request.scope.get("route"), "path", None)
    if isinstance(path, str):
        return path
    endpoint = request.scope.get("endpoint")
    if endpoint is not None:
        for route in request.app.routes:
            if isinstance(route, Mount) and route.app is endpoint:
                return route.path
    return UNMATCHED


def create_app(
    config: Config | None = None,
    strings: Strings | None = None,
    pages: PageCatalog | None = None,
    builder: SeedBuilder | None = None,
    rate_limiter: RateLimiter | None = None,
    discord: DiscordClient | None = None,
    timings: TimingSink | None = None,
    version: str | None = None,
) -> FastAPI:
    """Build the app.

    With no config, reads it from the environment; with no strings or pages, loads those
    catalogs; with no builder, makes one from the config when the app starts; with no rate
    limiter, uses the generate limits in `server/ratelimit.py`; with no Discord client,
    makes one when the config has credentials; with no version, asks git for the release
    the checkout is at. Raises ConfigError for settings the site refuses.
    """
    config = config if config is not None else Config.from_env()
    config.validate()
    if discord is None and config.discord_enabled:
        discord = DiscordClient(
            config.discord_client_id or "", config.discord_client_secret or ""
        )
    strings = strings if strings is not None else Strings.load()
    pages = pages if pages is not None else PageCatalog.load()
    site_version = version if version is not None else read_site_version(REPO_ROOT)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(config.database)
        try:
            before = db.version()
            after = db.migrate()
            if after != before:
                log.info("schema migrated %d to %d", before, after)
            unfilled = unfilled_seeds(db)
            if unfilled:
                raise DatabaseError(
                    f"{len(unfilled)} seeds, such as {unfilled[0]}, {NOT_BACKFILLED}"
                )
            app.state.db = db
            seed_builder = (
                builder if builder is not None else SeedBuilder.from_config(config)
            )
            try:
                await run_in_threadpool(seed_builder.warm)
            except BuilderUnavailableError as problem:
                log.error("cannot build seeds until this is fixed: %s", problem)
            app.state.builder = seed_builder
            sink = timings if timings is not None else TimingSink(db)
            app.state.timings = sink
            flusher = (
                asyncio.create_task(flush_periodically(sink))
                if sink.flush_seconds is not None
                else None
            )
            try:
                yield
            finally:
                # A deploy restart loses nothing that has buffered since the last flush.
                if flusher is not None:
                    flusher.cancel()
                    with suppress(asyncio.CancelledError):
                        await flusher
                try:
                    await run_in_threadpool(sink.flush)
                except Exception:
                    log.exception("could not write the last request timings")
        finally:
            db.close()

    app = FastAPI(
        title="NES Open Randomizer",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.strings = strings
    app.state.pages = pages
    app.state.rate_limiter = (
        rate_limiter
        if rate_limiter is not None
        else RateLimiter(GENERATE_CAPACITY, GENERATE_REFILL_SECONDS)
    )
    app.state.discord = discord
    app.state.renders = HoleRenders(config.rangefinder_dir)
    # Without a configured secret (development and tests; validate() insists on one for
    # Discord) sessions are signed with a secret that lasts as long as the process.
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret or secrets.token_urlsafe(32),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=config.base_url.startswith("https://"),
    )

    @app.middleware("http")
    async def record_timing(request: Request, call_next):
        """Time every request into `app.state.timings`, and log the notable ones.

        Added last, so it wraps every other middleware and sees the whole server's
        time. Caddy already logs that a request happened and how long the client
        waited; what this adds is the outcome and the phases inside one.
        """
        started = time.perf_counter()
        token = request_id.set(secrets.token_hex(8))
        sample = Sample(method=request.method, request_id=request_id.get())
        request.state.sample = sample
        failed = False
        try:
            try:
                response = await call_next(request)
            except Exception:
                failed = True
                sample.status = 500
                sample.outcome = EXCEPTION
                raise
            else:
                sample.status = response.status_code
                response.headers["X-Request-Id"] = sample.request_id
                return response
            finally:
                sample.route = route_template(request)
                sample.total_ms = (time.perf_counter() - started) * 1000
                request.app.state.timings.record(sample)
                if sample.notable:
                    log.log(
                        logging.ERROR if sample.status >= 500 else logging.INFO,
                        "%s %s %d in %.0fms",
                        sample.method,
                        sample.route,
                        sample.status,
                        sample.total_ms,
                        exc_info=failed,
                        extra={"outcome": sample.outcome, "phases": sample.phases},
                    )
        finally:
            request_id.reset(token)

    def site_url(path: str) -> str:
        """`path` as a full URL on the public site, which a preview card's tags must hold."""
        return config.base_url.rstrip("/") + path

    def sign_in_context(request: Request) -> dict:
        """What base.html needs on every page: the player, where to come back to, and the
        page's own address for its preview card."""
        path = request.url.path
        here = path + (f"?{request.url.query}" if request.url.query else "")
        return {
            "user": current_user(request),
            "sign_in_enabled": config.sign_in_enabled,
            "return_path": "/" if path.startswith("/auth/") else here,
            "content_pages": pages.listed,
            "site_version": site_version,
            # without the query: a round's card names its permalink, not `?recorded`
            "page_url": site_url(path),
        }

    templates = Jinja2Templates(
        directory=TEMPLATES_DIR, context_processors=[sign_in_context]
    )
    templates.env.globals["t"] = strings.html
    templates.env.globals["t_plain"] = strings.plain
    templates.env.globals["static_url"] = StaticVersions(STATIC_DIR).url
    templates.env.globals["site_url"] = site_url
    templates.env.globals["logo"] = LOGO
    templates.env.globals["logo_size"] = LOGO_SIZE
    templates.env.filters["timestamp"] = timestamp
    templates.env.filters["calendar_date"] = calendar_date
    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    # Not checked at startup: golf-site refuses to run without the renders, and tests
    # build apps on a fresh clone that has none.
    app.mount(
        RANGEFINDER_DATA_URL,
        CachedStaticFiles(directory=config.rangefinder_dir, check_dir=False),
        name="rangefinder_data",
    )
    app.include_router(admin_router(templates))
    app.include_router(host_router(templates))
    app.include_router(claim_router(templates))
    app.include_router(site_router(templates, pages))
    app.include_router(seed_router(templates, mystery_seeds=config.mystery_seeds))
    app.include_router(round_router(templates))
    app.include_router(account_router(templates))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 and not request.url.path.endswith(MACHINE_SUFFIXES):
            return templates.TemplateResponse(
                request, "not_found.html", {"page": None}, status_code=404
            )
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> Response:
        """The page for an unhandled exception. The timing middleware has already logged it.

        Starlette re-raises the exception after sending this, so the server still sees it.
        A machine path keeps Starlette's plain text, which its script reports by status.
        If the page itself fails, as it would with the database down, so does this: the
        plain-text default goes out instead.
        """
        sample = getattr(request.state, "sample", None)
        request_ref = sample.request_id if sample is not None else ""
        headers = {"X-Request-Id": request_ref} if request_ref else None
        if not request.url.path.endswith(MACHINE_SUFFIXES):
            try:
                return templates.TemplateResponse(
                    request,
                    "server_error.html",
                    {"page": None, "request_id": request_ref},
                    status_code=500,
                    headers=headers,
                )
            except Exception:
                log.exception("the server error page failed to render")
        return PlainTextResponse(
            "Internal Server Error", status_code=500, headers=headers
        )

    return app
