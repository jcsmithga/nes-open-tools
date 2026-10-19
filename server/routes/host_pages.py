"""The host pages' routes: `/host/...`, for the signed-in users `GOLF_HOST_USERS` lists and
every admin.

A host builds a seed's ROM for the players who claim its slots from their phones
(docs/planning/hosted_roms.md). `/host` lists the host's hosted ROMs and creates one from a
seed's link or id; `/host/<id>` is one hosted ROM: its claim code, its claimants, player 1's
name and the download. A host sees only their own hosted ROMs, an admin any. Anyone else
gets the ordinary not-found page.

Like the admin pages, these are exempt from the strings catalog: their English is in their
templates. The one catalog text is `download.js`'s status messages, which the hosting page
embeds as the seed page does. Actions are POSTs that redirect back with `?result=`.
"""

import logging
from dataclasses import replace
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from golf.core.patches.sram_defaults import NAME_CHARS, NAME_LENGTH
from golf.qr.encoder import MAX_CHARS
from golf.qr.svg import code_svg
from golf.randomizer.build import credentials_for, credentials_for_slots
from golf.randomizer.manifest import ClubRules
from golf.randomizer.roms import vanilla_rom

from ..auth import current_user
from ..builder import BuilderUnavailableError, SeedBuilder
from ..db import Database
from ..entries import upsert_entry
from ..forms import (
    DownloadState,
    FormError,
    SavedSettings,
    check_rom_hashes,
    fit,
    player_options_from_state,
)
from ..hosting import (
    TOKEN_LENGTH,
    HostedRom,
    HostingError,
    create_hosted_rom,
    hosted_roms_for_host,
    load_hosted_rom,
    mark_downloaded,
    recent_seeds,
    release_slot,
    save_hosted_options,
)
from ..ids import is_id
from ..seeds import SeedRow, load_seed, load_unfinished_ips
from ..strings import Strings
from ..timings import OK, Sample
from ..users import User, load_user
from ..views import hosted_download_stem
from .common import json_refusal, not_found, outcome
from .seed_pages import DOWNLOAD_SCRIPT_STRINGS, SEED_WITHDRAWN, UNAVAILABLE

log = logging.getLogger(__name__)

#: download refusals download.js has no notice for, which it shows as a failure
EXPIRED = "expired"
NO_CLAIMS = "no_claims"


def require_host(request: Request) -> User:
    """The signed-in host, or a 404 for anyone else."""
    user = current_user(request)
    if user is None or not request.app.state.config.is_host(user.discord_id):
        raise HTTPException(status_code=404)
    return user


Host = Annotated[User, Depends(require_host)]


def claim_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/claim/{token}"


def seed_id_from(text: str) -> str:
    """The seed id in a pasted seed link, or the text itself: `.../h/<id>[/...]` or `<id>`."""
    text = text.strip()
    parts = [part for part in urlsplit(text).path.split("/") if part]
    if "h" in parts[:-1]:
        return parts[parts.index("h") + 1]
    return parts[-1] if parts else ""


def valid_name(text: str) -> str | None:
    """The name as the ROM stores it, upper case, or None for one the game cannot show."""
    name = text.strip().upper()
    if not name or len(name) > NAME_LENGTH or set(name) - set(NAME_CHARS):
        return None
    return name


def club_warning(rules: ClubRules) -> bool:
    """Whether the seed's club rules break the event's rule: none, or a locked bag."""
    return rules != ClubRules() and rules.required_bag is None


def _redirect(path: str, result: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?{urlencode({'result': result})}", status_code=303)


def host_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/host", dependencies=[Depends(require_host)])

    def render(request: Request, template: str, context: dict):
        context = {
            "page": None,
            "result": request.query_params.get("result"),
            **context,
        }
        return templates.TemplateResponse(request, f"host/{template}", context)

    def db_of(request: Request) -> Database:
        return request.app.state.db

    def owned(request: Request, public_id: str, host: User) -> HostedRom:
        """The host's hosted ROM, or any for an admin; a 404 otherwise."""
        hosted = load_hosted_rom(db_of(request), public_id)
        if hosted is None or (
            hosted.host_id != host.id
            and not request.app.state.config.is_admin(host.discord_id)
        ):
            raise not_found()
        return hosted

    def seed_of(request: Request, hosted: HostedRom) -> SeedRow:
        row = load_seed(db_of(request), hosted.seed_id)
        if row is None:  # pragma: no cover - seeds are never deleted
            raise not_found()
        return row

    def player_name(hosted: HostedRom) -> str:
        return (hosted.options or SavedSettings()).player_name

    @router.get("", response_class=HTMLResponse)
    def index(request: Request, host: Host):
        db = db_of(request)
        listed = []
        for hosted in hosted_roms_for_host(db, host.id):
            row = load_seed(db, hosted.seed_id)
            listed.append((hosted, row))
        return render(
            request, "index.html", {"listed": listed, "recent": recent_seeds(db)}
        )

    @router.post("")
    def create(request: Request, host: Host, seed: Annotated[str, Form()] = ""):
        base_url = request.app.state.config.base_url
        if len(claim_url(base_url, "x" * TOKEN_LENGTH).encode()) > MAX_CHARS:
            return _redirect("/host", "base_url_too_long")
        seed_id = seed_id_from(seed)
        if not is_id(seed_id):
            return _redirect("/host", "unknown_seed")
        try:
            hosted = create_hosted_rom(db_of(request), seed_id, host.id)
        except HostingError as problem:
            return _redirect("/host", f"{problem.reason}_seed")
        return RedirectResponse(f"/host/{hosted.public_id}", status_code=303)

    @router.get("/{public_id}", response_class=HTMLResponse)
    def hosted_page(request: Request, public_id: str, host: Host):
        hosted = owned(request, public_id, host)
        row = seed_of(request, hosted)
        strings: Strings = request.app.state.strings
        expired = hosted.expired()
        free = any(not slot.claimed for slot in hosted.slots)
        show_code = free and hosted.downloaded_at is None and not expired
        show_code = show_code and not hosted.withdrawn
        url = claim_url(request.app.state.config.base_url, hosted.token)
        name = player_name(hosted)
        required = tuple(vanilla_rom(rom) for rom in row.required_roms)
        return render(
            request,
            "hosted.html",
            {
                "hosted": hosted,
                "seed": row,
                "expired": expired,
                "show_code": show_code,
                "claim_url": url,
                "claim_svg": Markup(code_svg(url)) if show_code else None,
                "player_name": name,
                "name_max": NAME_LENGTH,
                "filename": hosted_download_stem(row, name, hosted.public_id) + ".nes",
                "club_warning": club_warning(row.manifest.course.clubs),
                "can_download": any(slot.claimed for slot in hosted.slots)
                and not expired
                and not hosted.withdrawn,
                "required_roms": required,
                "rom_details": {
                    rom.id: {"title": rom.title, "sha1": rom.sha1} for rom in required
                },
                "download_strings": strings.for_script(DOWNLOAD_SCRIPT_STRINGS),
            },
        )

    @router.post("/{public_id}/name")
    def save_name(
        request: Request,
        public_id: str,
        host: Host,
        player_name: Annotated[str, Form()] = "",
    ):
        hosted = owned(request, public_id, host)
        name = valid_name(player_name)
        if name is None:
            return _redirect(f"/host/{public_id}", "invalid_name")
        options = hosted.options or SavedSettings()
        save_hosted_options(
            db_of(request),
            public_id,
            SavedSettings.from_json({**options.to_json(), "name": name}),
        )
        return _redirect(f"/host/{public_id}", "name_saved")

    @router.post("/{public_id}/slots/{slot}/release")
    def release(request: Request, public_id: str, slot: int, host: Host):
        owned(request, public_id, host)
        try:
            release_slot(db_of(request), public_id, slot, host.id)
        except ValueError:
            raise not_found() from None
        return _redirect(f"/host/{public_id}", "released")

    @router.post("/{public_id}/patch.ips")
    async def hosted_patch(request: Request, public_id: str, host: Host):
        hosted = owned(request, public_id, host)
        db = db_of(request)
        row = seed_of(request, hosted)
        sample: Sample = request.state.sample
        if hosted.withdrawn:
            outcome(request, SEED_WITHDRAWN)
            return json_refusal(410, SEED_WITHDRAWN)
        if hosted.expired():
            outcome(request, EXPIRED)
            return json_refusal(410, EXPIRED)
        players = [
            user
            for user_id in hosted.claimants
            if user_id is not None and (user := load_user(db, user_id)) is not None
        ]
        if not players:
            outcome(request, NO_CLAIMS)
            return json_refusal(409, NO_CLAIMS)

        manifest = row.manifest
        rules, abi = manifest.course.clubs, manifest.finish_abi_version
        submitted = DownloadState.from_form(await request.form())
        # the hosted options, fitted to the seed: a locked bag replaces the bag and banned
        # clubs leave it; the form only carries the ROM hashes download.js adds
        state = replace(
            fit(hosted.options or SavedSettings(), rules, abi).state,
            rom_hashes=submitted.rom_hashes,
        )
        try:
            check_rom_hashes(state, row.required_roms)
        except FormError as problem:
            outcome(request, problem.reason)
            return json_refusal(403, problem.reason, problem.values)
        try:
            options = player_options_from_state(state, rules, abi)
        except FormError as problem:
            outcome(request, problem.reason)
            return json_refusal(400, problem.reason, problem.values)

        unfinished_ips = load_unfinished_ips(db, row.id)
        if unfinished_ips is None:  # pragma: no cover - seeds are never deleted
            raise not_found()
        with sample.phase("entry"):
            entries = [upsert_entry(db, row.id, user.id, options) for user in players]
        if len(players) == 2:
            # each claimant signs with their own slot 0 key: their own round, whichever
            # position they play (server/submissions.py)
            credentials = credentials_for_slots(
                row.qr_seed_id,
                (players[0].player_id, entries[0].keys[0]),
                (players[1].player_id, entries[1].keys[0]),
            )
        else:
            credentials = credentials_for(
                row.qr_seed_id, players[0].player_id, entries[0].keys
            )
        seed_builder: SeedBuilder = request.app.state.builder
        try:
            with sample.phase("finish"):
                patch = await run_in_threadpool(
                    seed_builder.finish, manifest, unfinished_ips, options, credentials
                )
        except BuilderUnavailableError as problem:
            log.error("cannot finish a hosted download: %s", problem)
            outcome(request, UNAVAILABLE)
            return json_refusal(503, UNAVAILABLE)
        current = load_seed(db, row.id)
        if current is None or current.withdrawn:
            outcome(request, SEED_WITHDRAWN)
            return json_refusal(410, SEED_WITHDRAWN)
        mark_downloaded(db, public_id, host.id, hosted.claimants)
        outcome(request, OK)
        stem = hosted_download_stem(row, options.player_name, public_id)
        return Response(
            patch,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{stem}.ips"'},
        )

    return router
