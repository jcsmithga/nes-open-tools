"""The daily seed's addresses: `/daily`, the Daily Seed; `/daily/<YYYY-MM-DD>`, a day's
seed; and `/daily/calendar`, the calendar of past dailies (docs/competitive/design.md,
On the site). A day's own page is its seed's page, which these redirect to."""

from datetime import date, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from golf.randomizer.daily import daily_date

from ..dailies import (
    daily_seed,
    first_daily_day,
    load_daily,
    month_bounds,
    opened_dailies,
    utc_text,
)
from ..db import Database
from ..views import calendar_view
from .common import not_found

CALENDAR = "/daily/calendar"


def daily_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def now(request: Request) -> datetime:
        return request.app.state.clock()

    # A redirect, never cached: the Daily Seed changes at each rollover.
    @router.get("/daily")
    def the_daily_seed(request: Request):
        found = daily_seed(request.app.state.db, now(request))
        target = CALENDAR if found is None else f"/h/{found.seed_id}"
        return RedirectResponse(
            target, status_code=303, headers={"Cache-Control": "no-store"}
        )

    @router.get("/daily/calendar", response_class=HTMLResponse)
    def calendar(request: Request, month: str | None = None):
        db: Database = request.app.state.db
        moment = now(request)
        today = daily_date(moment)
        if month is None:
            shown = today.replace(day=1)
        else:
            try:
                shown = date.fromisoformat(f"{month}-01")
            except ValueError:
                raise not_found() from None
            if shown > today:
                raise not_found()
        first, last = month_bounds(shown.year, shown.month)
        view = calendar_view(
            shown, opened_dailies(db, first, last, moment), first_daily_day(db), today
        )
        return templates.TemplateResponse(
            request, "daily_calendar.html", {"page": "daily", "calendar": view}
        )

    @router.get("/daily/{day}")
    def day_seed(request: Request, day: str):
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            raise not_found() from None
        found = load_daily(request.app.state.db, parsed)
        if found is None or utc_text(now(request)) < found.opens_at:
            raise not_found()
        return RedirectResponse(
            f"/h/{found.seed_id}",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    return router
