"""The claim page: `/claim/<token>`, where a player takes a slot of a hosted ROM.

A host's page shows the claim link as a QR code; a player scans it with their phone, signs
in if they are not, and confirms (docs/planning/hosted_roms.md). The GET only shows the
state; the POST claims and redirects back to it. The token is the claim's only secret, so
the page is never cached and sends no referrer.
"""

from contextlib import suppress

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import current_user
from ..db import Database
from ..hosting import HostedRom, HostingError, claim_slot, load_by_token
from ..seeds import load_seed
from ..users import User, load_user

#: what the claim page shows, as its `data-claim-state`
UNKNOWN = "unknown"
CLAIMED = "claimed"
WITHDRAWN = "withdrawn"
EXPIRED = "expired"
FULL = "full"
SIGN_IN = "sign_in"
CONFIRM = "confirm"

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def claim_state(hosted: HostedRom | None, user: User | None) -> str:
    """What the page shows a viewer: a slot they hold first, then why no claim is possible."""
    if hosted is None:
        return UNKNOWN
    if user is not None and user.id in hosted.claimants:
        return CLAIMED
    if hosted.withdrawn:
        return WITHDRAWN
    if hosted.expired():
        return EXPIRED
    if all(slot.claimed for slot in hosted.slots):
        return FULL
    return SIGN_IN if user is None else CONFIRM


def claim_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/claim/{token}", response_class=HTMLResponse)
    def claim_page(request: Request, token: str):
        db: Database = request.app.state.db
        hosted = load_by_token(db, token)
        user = current_user(request)
        state = claim_state(hosted, user)
        context: dict = {"page": None, "state": state}
        if hosted is not None:
            seed = load_seed(db, hosted.seed_id)
            host = load_user(db, hosted.host_id)
            context |= {
                "seed": seed,
                "host_name": host.display_name if host is not None else "",
                "slot": hosted.claimants.index(user.id)
                if state == CLAIMED and user
                else None,
            }
        return templates.TemplateResponse(
            request,
            "claim.html",
            context,
            status_code=404 if state == UNKNOWN else 200,
            headers=HEADERS,
        )

    @router.post("/claim/{token}")
    def claim(request: Request, token: str):
        user = current_user(request)
        if user is not None:
            # a refused claim needs no message here: the page it redirects to shows why
            with suppress(HostingError):
                claim_slot(request.app.state.db, token, user.id)
        return RedirectResponse(f"/claim/{token}", status_code=303, headers=HEADERS)

    return router
