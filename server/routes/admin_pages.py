"""The admin pages' routes: `/admin/...`, for the signed-in users `GOLF_ADMIN_USERS` lists.

Anyone else gets the ordinary not-found page, so nothing tells a visitor the pages exist.
The pages are for admins only and are exempt from the strings catalog: their English is in
their templates. Actions are POSTs that redirect back with `?result=`; the session cookie
is SameSite=lax, so a cross-site POST arrives signed out and is not found. See
docs/randomizer_devplan.md, "Users and access".
"""

import csv
import io
from collections.abc import Sequence
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..admin import (
    METRICS_DAYS,
    TREND_DAYS,
    SeedScores,
    actions_page,
    counts,
    metrics_view,
    round_detail,
    rounds_page,
    seed_detail,
    seed_scores,
    seeds_page,
    user_detail,
    users_page,
    voided_page,
)
from ..auth import current_user
from ..builder import SeedBuilder
from ..db import Database
from ..rounds import (
    SlotTakenError,
    flag_round,
    restore_round,
    unflag_round,
    void_round,
)
from ..seeds import (
    SeedAlreadyWithdrawnError,
    SeedNotWithdrawnError,
    restore_seed,
    withdraw_seed,
)
from ..timings import RETENTION_DAYS
from ..users import User


def require_admin(request: Request) -> User:
    """The signed-in admin, or a 404 for anyone else."""
    user = current_user(request)
    if user is None or not request.app.state.config.is_admin(user.discord_id):
        raise HTTPException(status_code=404)
    return user


Admin = Annotated[User, Depends(require_admin)]
PageNumber = Annotated[int, Query(ge=1)]
#: the metrics window cannot exceed the raw samples' retention period
MetricsDays = Annotated[int, Query(ge=1, le=RETENTION_DAYS)]
Note = Annotated[str, Form()]


def _redirect(path: str, result: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?{urlencode({'result': result})}", status_code=303)


#: the hole columns of a scores CSV
HOLE_COLUMNS = tuple(str(number) for number in range(1, 19))


def scores_csv_text(scores: SeedScores) -> str:
    """A seed's scores as CSV: a par row, then one row per round, its holes as columns.

    Made for a spreadsheet: a best-ball score is a MIN over two players' hole columns.
    `kind` is `own` for a player's own round and `teammate` for one played as player 2
    on their ROM, whose player the site does not know.
    """

    def nines(holes: Sequence[int]) -> list[int]:
        return [sum(holes[:9]), sum(holes[9:]), sum(holes)]

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        ["round", "player", "username", "kind", "received_at", "flagged"]
        + list(HOLE_COLUMNS)
        + ["out", "in", "total", "putts"]
    )
    writer.writerow(
        ["par", "", "", "", "", ""] + list(scores.pars) + nines(scores.pars) + [""]
    )
    for row in scores.rounds:
        writer.writerow(
            [
                row.public_id,
                row.user_name,
                row.username,
                "own" if row.slot == 0 else "teammate",
                row.received_at,
                "yes" if row.flagged else "",
            ]
            + list(row.strokes)
            + nines(row.strokes)
            + [row.total_putts]
        )
    return out.getvalue()


def admin_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    def render(request: Request, template: str, context: dict, status_code: int = 200):
        context = {
            "page": None,
            "result": request.query_params.get("result"),
            **context,
        }
        return templates.TemplateResponse(
            request, f"admin/{template}", context, status_code=status_code
        )

    def db_of(request: Request) -> Database:
        return request.app.state.db

    def not_found() -> HTTPException:
        return HTTPException(status_code=404)

    @router.get("", response_class=HTMLResponse)
    def index(request: Request):
        return render(request, "index.html", {"counts": counts(db_of(request))})

    @router.get("/seeds", response_class=HTMLResponse)
    def seeds(request: Request, page: PageNumber = 1):
        return render(
            request, "seeds.html", {"listing": seeds_page(db_of(request), page)}
        )

    def seed_page(
        request: Request, seed_id: str, error: str | None = None, status_code: int = 200
    ):
        seed_builder: SeedBuilder = request.app.state.builder
        detail = seed_detail(db_of(request), seed_id, seed_builder.catalog)
        if detail is None:
            raise not_found()
        return render(
            request,
            "seed.html",
            {"detail": detail, "error": error},
            status_code=status_code,
        )

    @router.get("/seeds/{seed_id}", response_class=HTMLResponse)
    def seed(request: Request, seed_id: str):
        return seed_page(request, seed_id)

    @router.get("/seeds/{seed_id}/scores.csv")
    def scores_csv(request: Request, seed_id: str):
        scores = seed_scores(db_of(request), seed_id)
        if scores is None:
            raise not_found()
        return Response(
            scores_csv_text(scores),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="scores_{seed_id}.csv"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/seeds/{seed_id}/withdraw")
    def withdraw(request: Request, seed_id: str, admin: Admin, note: Note = ""):
        try:
            withdraw_seed(db_of(request), seed_id, admin.id, note)
        except KeyError:
            raise not_found() from None
        except SeedAlreadyWithdrawnError:
            return seed_page(
                request, seed_id, "Not withdrawn: the seed is already withdrawn.", 409
            )
        return _redirect(f"/admin/seeds/{seed_id}", "withdrawn")

    @router.post("/seeds/{seed_id}/restore")
    def restore_seed_route(request: Request, seed_id: str, admin: Admin):
        try:
            restore_seed(db_of(request), seed_id, admin.id)
        except KeyError:
            raise not_found() from None
        except SeedNotWithdrawnError:
            return seed_page(
                request, seed_id, "Not restored: the seed is already active.", 409
            )
        return _redirect(f"/admin/seeds/{seed_id}", "seed_restored")

    @router.get("/rounds", response_class=HTMLResponse)
    def rounds(request: Request, page: PageNumber = 1, flagged: bool = False):
        listing = rounds_page(db_of(request), page, flagged_only=flagged)
        return render(request, "rounds.html", {"listing": listing, "flagged": flagged})

    @router.get("/rounds/{public_id}", response_class=HTMLResponse)
    def round_page(request: Request, public_id: str):
        detail = round_detail(db_of(request), public_id)
        if detail is None:
            raise not_found()
        return render(request, "round.html", {"detail": detail})

    @router.post("/rounds/{public_id}/flag")
    def flag(request: Request, public_id: str, admin: Admin, note: Note = ""):
        try:
            flag_round(db_of(request), public_id, admin.id, note)
        except KeyError:
            raise not_found() from None
        return _redirect(f"/admin/rounds/{public_id}", "flagged")

    @router.post("/rounds/{public_id}/unflag")
    def unflag(request: Request, public_id: str, admin: Admin):
        try:
            unflag_round(db_of(request), public_id, admin.id)
        except KeyError:
            raise not_found() from None
        return _redirect(f"/admin/rounds/{public_id}", "unflagged")

    @router.post("/rounds/{public_id}/void")
    def void(request: Request, public_id: str, admin: Admin, note: Note = ""):
        try:
            void_round(db_of(request), public_id, admin.id, note)
        except KeyError:
            raise not_found() from None
        return _redirect("/admin/voided", "voided")

    @router.post("/rounds/{public_id}/restore")
    def restore(request: Request, public_id: str, admin: Admin):
        try:
            restore_round(db_of(request), public_id, admin.id)
        except KeyError:
            raise not_found() from None
        except SlotTakenError:
            return _redirect("/admin/voided", "slot_taken")
        return _redirect(f"/admin/rounds/{public_id}", "restored")

    @router.get("/users", response_class=HTMLResponse)
    def users(request: Request, page: PageNumber = 1):
        return render(
            request, "users.html", {"listing": users_page(db_of(request), page)}
        )

    @router.get("/users/{user_id}", response_class=HTMLResponse)
    def user(request: Request, user_id: int):
        detail = user_detail(db_of(request), user_id)
        if detail is None:
            raise not_found()
        return render(request, "user.html", {"detail": detail})

    @router.get("/metrics", response_class=HTMLResponse)
    def metrics(request: Request, days: MetricsDays = METRICS_DAYS):
        return render(
            request,
            "metrics.html",
            {
                "metrics": metrics_view(db_of(request), days),
                "max_days": RETENTION_DAYS,
                "trend_days": TREND_DAYS,
            },
        )

    @router.get("/activity", response_class=HTMLResponse)
    def activity(request: Request, page: PageNumber = 1):
        return render(
            request, "activity.html", {"listing": actions_page(db_of(request), page)}
        )

    @router.get("/voided", response_class=HTMLResponse)
    def voided(request: Request, page: PageNumber = 1):
        return render(
            request, "voided.html", {"listing": voided_page(db_of(request), page)}
        )

    return router
