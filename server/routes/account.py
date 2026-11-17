"""A player's own pages: `/me` with its saved download settings, and signing in and out
under `/auth`."""

import logging
import secrets
from dataclasses import replace
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import (
    DEV_DISCORD_PREFIX,
    DEV_NAME,
    SESSION_NEXT,
    SESSION_STATE,
    DiscordClient,
    DiscordError,
    current_user,
    safe_next,
    start_session,
)
from ..config import Config
from ..db import Database
from ..download_settings import forget_settings, load_settings, save_settings
from ..entries import entries_for_user
from ..forms import DownloadState, FormError, saved_from_state
from ..ratings import player_index
from ..rounds import rounds_for_user
from ..timings import OK
from ..users import User, sign_in
from ..views import index_view, settings_view
from .common import (
    DOWNLOAD_COOKIE,
    delete_download_cookie,
    not_found,
    outcome,
    saved_settings,
    set_download_cookie,
)

#: /me shows one notice per value: one of these, or a download FormError reason
SETTINGS_SAVED = "saved"
SETTINGS_FORGOTTEN = "forgotten"

#: the name /auth/login?as= signs in as when it names none
DEFAULT_DEV_NAME = "dev"
#: sign_in_failed.html shows one notice per value
SIGN_IN_EXPIRED = "expired"
SIGN_IN_UNAVAILABLE = "unavailable"

log = logging.getLogger(__name__)


def redirect_uri(config: Config) -> str:
    return config.base_url.rstrip("/") + "/auth/callback"


def account_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def me_page(
        request: Request,
        user: User,
        *,
        state: DownloadState | None = None,
        result: str | None = None,
        status_code: int = 200,
    ):
        """Render /me with saved settings or the submitted values after an error."""
        db: Database = request.app.state.db
        has_saved = (
            load_settings(db, user.id) is not None or DOWNLOAD_COOKIE in request.cookies
        )
        settings = settings_view(saved_settings(request), has_saved)
        if state is not None:
            settings = replace(settings, state=state)
        return templates.TemplateResponse(
            request,
            "me.html",
            {
                "page": "me",
                "entries": entries_for_user(db, user.id),
                "rounds": rounds_for_user(db, user.id),
                "settings": settings,
                "index": index_view(
                    player_index(db, user.id, request.app.state.ratings.parameters)
                ),
                "result": result,
            },
            status_code=status_code,
        )

    @router.get("/me", response_class=HTMLResponse)
    def me(request: Request):
        config: Config = request.app.state.config
        user = current_user(request)
        if user is None:
            if not config.sign_in_enabled:
                raise not_found()
            return RedirectResponse("/auth/login?next=/me", status_code=303)
        return me_page(request, user, result=request.query_params.get("result"))

    def me_redirect(result: str) -> RedirectResponse:
        return RedirectResponse(
            f"/me?{urlencode({'result': result})}#download-settings", status_code=303
        )

    @router.post("/me/download-settings")
    async def me_save_settings(request: Request):
        user = current_user(request)
        if user is None:
            raise not_found()
        state = DownloadState.from_form(await request.form())
        try:
            saved = saved_from_state(state, saved_settings(request))
        except FormError as problem:
            outcome(request, problem.reason)
            return me_page(
                request, user, state=state, result=problem.reason, status_code=400
            )
        save_settings(request.app.state.db, user.id, saved)
        outcome(request, OK)
        response = me_redirect(SETTINGS_SAVED)
        set_download_cookie(request, response, saved)
        return response

    @router.post("/me/download-settings/forget")
    def me_forget_settings(request: Request):
        user = current_user(request)
        if user is None:
            raise not_found()
        forget_settings(request.app.state.db, user.id)
        outcome(request, OK)
        response = me_redirect(SETTINGS_FORGOTTEN)
        delete_download_cookie(request, response)
        return response

    def sign_in_failed(request: Request, reason: str, status_code: int):
        return templates.TemplateResponse(
            request,
            "sign_in_failed.html",
            {"page": None, "reason": reason},
            status_code=status_code,
        )

    @router.get("/auth/login")
    def auth_login(
        request: Request,
        next: str | None = None,
        as_: str | None = Query(None, alias="as"),
    ):
        config: Config = request.app.state.config
        if not config.sign_in_enabled:
            raise not_found()
        return_to = safe_next(next)
        if config.dev_login:
            name = as_ if as_ is not None else DEFAULT_DEV_NAME
            if not DEV_NAME.fullmatch(name):
                raise HTTPException(status_code=400)
            user = sign_in(
                request.app.state.db, DEV_DISCORD_PREFIX + name, name, None, None
            )
            start_session(request, user)
            return RedirectResponse(return_to, status_code=303)
        state = secrets.token_urlsafe(32)
        request.session[SESSION_STATE] = state
        request.session[SESSION_NEXT] = return_to
        discord_client: DiscordClient = request.app.state.discord
        return RedirectResponse(
            discord_client.authorize_url(state, redirect_uri(config)), status_code=303
        )

    @router.get("/auth/callback")
    async def auth_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ):
        config: Config = request.app.state.config
        discord_client: DiscordClient | None = request.app.state.discord
        if config.dev_login or discord_client is None:
            raise not_found()
        expected = request.session.pop(SESSION_STATE, None)
        return_to = safe_next(request.session.pop(SESSION_NEXT, None))
        if not expected or not state or not secrets.compare_digest(expected, state):
            return sign_in_failed(request, SIGN_IN_EXPIRED, 400)
        if error is not None or not code:
            # The player turned Discord down: back where they were, still signed out.
            return RedirectResponse(return_to, status_code=303)
        try:
            identity = await discord_client.identify(code, redirect_uri(config))
        except DiscordError as problem:
            log.warning("Discord sign-in failed: %s", problem)
            return sign_in_failed(request, SIGN_IN_UNAVAILABLE, 502)
        user = sign_in(
            request.app.state.db,
            identity.id,
            identity.username,
            identity.global_name,
            identity.avatar,
        )
        start_session(request, user)
        return RedirectResponse(return_to, status_code=303)

    @router.post("/auth/logout")
    async def auth_logout(request: Request):
        form = await request.form()
        next_value = form.get("next")
        request.session.clear()
        return RedirectResponse(
            safe_next(next_value if isinstance(next_value, str) else None),
            status_code=303,
        )

    return router
