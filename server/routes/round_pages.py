"""Recording a round and showing it: the scan a ROM's QR code links to, `/s/<scan>`, and
the round's permalink, `/r/<id>`."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import current_user
from ..dailies import scores_hidden
from ..db import Database
from ..rounds import VoidedRound, find_round
from ..seeds import load_seed
from ..submissions import MALFORMED, UNFINISHED, ScanError, submit_scan
from ..users import load_user
from ..views import round_view, voided_round_view
from .common import not_found

#: the query parameter `/s/` adds for the scan that recorded the round, which the round page
#: turns into its confirmation heading and a script then strips from the address bar
RECORDED = "recorded"


def round_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/s/{scan}", response_class=HTMLResponse)
    def scan(request: Request, scan: str):
        # A scan is a GET that records, so nothing may cache the answer, and a repeat of it
        # (the phone reopening the link, a chat unfurling it) must reach the same round. It
        # only submits: the round is shown by its permalink, which this redirects to.
        # A rejection has no round to point at, so it renders here.
        db: Database = request.app.state.db
        headers = {"Cache-Control": "no-store"}
        try:
            result = submit_scan(db, scan)
        except ScanError as rejection:
            status_code = 400 if rejection.reason in (MALFORMED, UNFINISHED) else 404
            return templates.TemplateResponse(
                request,
                "scan_rejected.html",
                {"page": None, "rejection": rejection.reason},
                status_code=status_code,
                headers=headers,
            )
        # `?recorded` only marks the scan that inserted the round, so the page can confirm it
        # once; the permalink the browser settles on carries no query.
        target = f"/r/{result.round.public_id}" + (f"?{RECORDED}" if result.new else "")
        return RedirectResponse(target, status_code=303, headers=headers)

    @router.get("/r/{round_id}", response_class=HTMLResponse)
    def round_page(request: Request, round_id: str):
        db: Database = request.app.state.db
        found = find_round(db, round_id)
        if found is None:
            raise not_found()
        row = load_seed(db, found.seed_id)
        player = load_user(db, found.user_id)
        if (
            row is None or player is None
        ):  # pragma: no cover - seeds and users are never deleted
            raise not_found()
        if isinstance(found, VoidedRound):
            return templates.TemplateResponse(
                request,
                "round_voided.html",
                {
                    "page": None,
                    "voided": voided_round_view(row, found, player.display_name),
                },
                status_code=410,
            )
        recorded = RECORDED in request.query_params
        viewer = current_user(request)
        # another player's scores on an open daily wait until the viewer has played it.
        # The scan that recorded the round shows it, since the phone that scanned may not
        # be signed in; hiding is a courtesy against spoilers, not a lock.
        hidden = (
            not recorded and (viewer is None or viewer.id != found.user_id)
        ) and scores_hidden(
            db,
            found.seed_id,
            None if viewer is None else viewer.id,
            request.app.state.clock(),
        )
        return templates.TemplateResponse(
            request,
            "round.html",
            {
                "page": None,
                "round": round_view(row, found, player.display_name, recorded),
                "scores_hidden": hidden,
            },
        )

    return router
