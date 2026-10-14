"""Generating a seed and what a seed serves: `/generate` and `/h/<id>`, with its manifest,
its IPS download and its yardage book."""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from golf.randomizer.build import credentials_for
from golf.randomizer.generate import GenerationError
from golf.randomizer.manifest import ManifestError
from golf.randomizer.request import RequestError, check_request, resolve

from ..auth import current_user
from ..builder import BuilderUnavailableError, SeedBuilder
from ..db import Database
from ..download_settings import save_settings
from ..entries import load_entry, upsert_entry
from ..forms import (
    DownloadState,
    FormError,
    FormState,
    SavedSettings,
    check_rom_hashes,
    player_options_from_state,
    request_from_state,
    to_save,
)
from ..ratelimit import client_key
from ..rounds import rounds_for_seed
from ..seeds import insert_seed, load_seed, load_seed_holes, load_unfinished_ips
from ..strings import Strings
from ..timings import OK, Sample
from ..views import download_stem, generate_options, seed_view
from ..yardage_book import yardage_book
from .common import (
    json_refusal,
    not_found,
    outcome,
    saved_settings,
    set_download_cookie,
)
from .site import RANGEFINDER_DATA_URL, RANGEFINDER_SCRIPT_STRINGS

#: the catalog prefix whose strings the seed page embeds for download.js
DOWNLOAD_SCRIPT_STRINGS = "seed.download.status"

#: generate.html shows one notice per value: a FormError reason, or one of these
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"
POOL_TOO_SMALL = "pool"
#: a seed kept as a permalink but no longer distributed
SEED_WITHDRAWN = "seed_withdrawn"

#: mystery.html shows one notice per value, beside the generate ones above
NO_FILE = "no_file"
TOO_LARGE = "too_large"
NOT_JSON = "not_json"
BAD_REQUEST = "bad_request"
#: the most bytes a request file may hold: a hand-written request is a few hundred
MYSTERY_MAX_BYTES = 16 * 1024

log = logging.getLogger(__name__)


def starting_settings(request: Request, seed_id: str) -> SavedSettings:
    """What a seed's download form starts from: this seed's entry over saved settings.

    The entry gives only name and clubs; the rest comes from the saved settings.
    """
    saved = saved_settings(request)
    user = current_user(request)
    if user is not None:
        entry = load_entry(request.app.state.db, seed_id, user.id)
        if entry is not None:
            saved = saved.with_entry(entry.player_name, entry.clubs)
    return saved


def seed_router(templates: Jinja2Templates, mystery_seeds: bool = False) -> APIRouter:
    """With `mystery_seeds` off, `/mystery` is not found and the generate page has no
    link to it."""
    router = APIRouter()

    def generate_page(
        request: Request,
        state: FormState,
        error: str | None = None,
        error_values: dict | None = None,
        status_code: int = 200,
    ):
        return templates.TemplateResponse(
            request,
            "generate.html",
            {
                "page": "generate",
                "options": generate_options(),
                "form": state,
                "mystery_seeds": mystery_seeds,
                "error": error,
                "error_values": error_values or {},
            },
            status_code=status_code,
        )

    @router.get("/generate", response_class=HTMLResponse)
    def generate_form(request: Request):
        return generate_page(request, FormState.default())

    def create_seed(request: Request, settings, asked: dict) -> str:
        """Generate, build and store a seed; runs in the threadpool."""
        seed_builder: SeedBuilder = request.app.state.builder
        sample: Sample = request.state.sample
        user = current_user(request)
        with sample.phase("generate"):
            manifest = seed_builder.generate(settings, asked)
        built = seed_builder.build(manifest, sample)
        with sample.phase("insert"):
            return insert_seed(
                request.app.state.db,
                manifest,
                built.unfinished_ips,
                built.holes,
                seed_builder.catalog,
                creator_id=None if user is None else user.id,
            )

    def mystery_page(
        request: Request,
        error: str | None = None,
        error_values: dict | None = None,
        status_code: int = 200,
    ):
        return templates.TemplateResponse(
            request,
            "mystery.html",
            {
                "page": "generate",
                "max_kb": MYSTERY_MAX_BYTES // 1024,
                "error": error,
                "error_values": error_values or {},
            },
            status_code=status_code,
        )

    @router.get("/mystery", response_class=HTMLResponse)
    def mystery_form(request: Request):
        if not mystery_seeds:
            raise not_found()
        return mystery_page(request)

    @router.post("/mystery", response_class=HTMLResponse)
    async def mystery_seed(request: Request):
        """A seed from a request file a player uploads (ADR 0016). The file is
        untrusted: its size is limited and it is parsed strictly."""
        if not mystery_seeds:
            raise not_found()
        upload = (await request.form()).get("request")
        if upload is None or isinstance(upload, str):
            outcome(request, NO_FILE)
            return mystery_page(request, NO_FILE, status_code=400)
        data = await upload.read(MYSTERY_MAX_BYTES + 1)
        if len(data) > MYSTERY_MAX_BYTES:
            outcome(request, TOO_LARGE)
            return mystery_page(request, TOO_LARGE, status_code=400)
        try:
            asked = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            outcome(request, NOT_JSON)
            return mystery_page(request, NOT_JSON, status_code=400)
        try:
            check_request(asked)
            settings = resolve(asked)
        except RequestError as problem:
            outcome(request, BAD_REQUEST)
            return mystery_page(
                request, BAD_REQUEST, {"problem": str(problem)}, status_code=400
            )

        user = current_user(request)
        if not request.app.state.rate_limiter.allow(
            client_key(request, None if user is None else user.id)
        ):
            outcome(request, RATE_LIMITED)
            return mystery_page(request, RATE_LIMITED, status_code=429)
        try:
            seed_id = await run_in_threadpool(create_seed, request, settings, asked)
        except GenerationError as problem:
            log.warning("a mystery request rolled no course: %s", problem)
            outcome(request, POOL_TOO_SMALL)
            return mystery_page(
                request,
                POOL_TOO_SMALL,
                {
                    "rolled": json.dumps(settings.to_json()),
                    "problem": str(problem),
                },
                status_code=400,
            )
        except BuilderUnavailableError as problem:
            log.error("cannot generate: %s", problem)
            outcome(request, UNAVAILABLE)
            return mystery_page(request, UNAVAILABLE, status_code=503)
        outcome(request, OK)
        return RedirectResponse(f"/h/{seed_id}", status_code=303)

    @router.post("/generate", response_class=HTMLResponse)
    async def generate_seed(request: Request):
        state = FormState.from_form(await request.form())
        try:
            asked = request_from_state(state)
            try:
                settings = resolve(asked)
            except ManifestError:  # pragma: no cover - the form checks what it sends
                raise FormError("invalid", field="settings") from None
        except FormError as problem:
            outcome(request, problem.reason)
            return generate_page(
                request, state, problem.reason, problem.values, status_code=400
            )

        user = current_user(request)
        user_id = user.id if user is not None else None
        # Only a submission that would make the server work spends a token.
        if not request.app.state.rate_limiter.allow(client_key(request, user_id)):
            outcome(request, RATE_LIMITED)
            return generate_page(request, state, RATE_LIMITED, status_code=429)

        try:
            seed_id = await run_in_threadpool(create_seed, request, settings, asked)
        except GenerationError as problem:
            log.warning(
                "generation found no pool: %s", problem, extra={"settings": settings}
            )
            outcome(request, POOL_TOO_SMALL)
            return generate_page(request, state, POOL_TOO_SMALL, status_code=400)
        except BuilderUnavailableError as problem:
            log.error("cannot generate: %s", problem)
            outcome(request, UNAVAILABLE)
            return generate_page(request, state, UNAVAILABLE, status_code=503)
        outcome(request, OK)
        return RedirectResponse(f"/h/{seed_id}", status_code=303)

    # Registered before the seed page, whose {seed_id} would otherwise match "<id>.json".
    @router.get("/h/{seed_id}.json")
    def seed_manifest(request: Request, seed_id: str):
        row = load_seed(request.app.state.db, seed_id)
        if row is None:
            raise not_found()
        return Response(row.manifest_json, media_type="application/json")

    # The book stays up for a withdrawn seed, as its page and manifest do.
    @router.get("/h/{seed_id}/book.json")
    def seed_book_metadata(request: Request, seed_id: str):
        db: Database = request.app.state.db
        row = load_seed(db, seed_id)
        if row is None:
            raise not_found()
        sample: Sample = request.state.sample
        with sample.phase("render"):
            book = yardage_book(
                db, row, request.app.state.renders, RANGEFINDER_DATA_URL
            )
        return JSONResponse(book, headers={"Cache-Control": "no-cache"})

    @router.get("/h/{seed_id}/book", response_class=HTMLResponse)
    def seed_book(request: Request, seed_id: str):
        row = load_seed(request.app.state.db, seed_id)
        if row is None:
            raise not_found()
        strings: Strings = request.app.state.strings
        return templates.TemplateResponse(
            request,
            "yardage_book.html",
            {
                "page": "seed",
                "seed_id": row.id,
                "magic_words": row.manifest.course.magic_words,
                "metadata_url": f"/h/{row.id}/book.json",
                "rangefinder_strings": strings.for_script(RANGEFINDER_SCRIPT_STRINGS),
            },
        )

    @router.get("/h/{seed_id}", response_class=HTMLResponse)
    def seed_page(request: Request, seed_id: str):
        row = load_seed(request.app.state.db, seed_id)
        if row is None:
            raise not_found()
        seed_builder: SeedBuilder = request.app.state.builder
        strings: Strings = request.app.state.strings
        view = seed_view(
            row,
            load_seed_holes(request.app.state.db, seed_id),
            seed_builder.curation,
            starting_settings(request, seed_id),
        )
        rounds = rounds_for_seed(request.app.state.db, seed_id)
        user = current_user(request)
        return templates.TemplateResponse(
            request,
            "seed.html",
            {
                "page": "seed",
                "seed": view,
                "rounds": rounds,
                # scores stay collapsed, so as not to spoil the seed, until the viewer
                # has recorded a round of their own on it
                "rounds_open": user is not None
                and any(r.user_id == user.id for r in rounds),
                "download_strings": strings.for_script(DOWNLOAD_SCRIPT_STRINGS),
            },
        )

    @router.post("/h/{seed_id}/patch.ips")
    async def seed_patch(request: Request, seed_id: str):
        row = load_seed(request.app.state.db, seed_id)
        if row is None:
            raise not_found()
        if row.withdrawn:
            outcome(request, SEED_WITHDRAWN)
            return json_refusal(410, SEED_WITHDRAWN)
        seed_builder: SeedBuilder = request.app.state.builder
        sample: Sample = request.state.sample
        manifest = row.manifest
        state = DownloadState.from_form(await request.form())
        try:
            check_rom_hashes(state, row.required_roms)
        except FormError as problem:
            outcome(request, problem.reason)
            return json_refusal(403, problem.reason, problem.values)
        try:
            options = player_options_from_state(
                state, manifest.course.clubs, manifest.finish_abi_version
            )
        except FormError as problem:
            outcome(request, problem.reason)
            return json_refusal(400, problem.reason, problem.values)

        db: Database = request.app.state.db
        unfinished_ips = load_unfinished_ips(db, seed_id)
        if unfinished_ips is None:  # pragma: no cover - seeds are never deleted
            raise not_found()
        # Signed in, the download enters the player in the seed and finishes with their
        # credentials; signed out, it finishes a guest ROM and records nothing.
        user = current_user(request)
        credentials = None
        if user is not None:
            with sample.phase("entry"):
                entry = upsert_entry(db, row.id, user.id, options)
            credentials = credentials_for(row.qr_seed_id, user.player_id, entry.keys)
        try:
            with sample.phase("finish"):
                patch = await run_in_threadpool(
                    seed_builder.finish, manifest, unfinished_ips, options, credentials
                )
        except BuilderUnavailableError as problem:
            log.error("cannot finish a download: %s", problem)
            outcome(request, UNAVAILABLE)
            return json_refusal(503, UNAVAILABLE)
        # A withdrawal while the threadpool was finishing withholds the result. A signed-in
        # request may already have created its entry, but no ROM leaves the server.
        current = load_seed(db, seed_id)
        if current is None:  # pragma: no cover - seeds are never deleted
            raise not_found()
        if current.withdrawn:
            outcome(request, SEED_WITHDRAWN)
            return json_refusal(410, SEED_WITHDRAWN)
        outcome(request, OK)
        response = Response(
            patch,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{download_stem(row)}.ips"'
            },
        )
        saved = to_save(
            options,
            manifest.course.clubs,
            manifest.finish_abi_version,
            saved_settings(request),
        )
        if user is not None:
            save_settings(db, user.id, saved)
        set_download_cookie(request, response, saved)
        return response

    return router
