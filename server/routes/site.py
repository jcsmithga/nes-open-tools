"""The pages that stand alone: home, ROM setup, the rangefinder, the Markdown pages, the
favicon and the health check."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from golf.randomizer.roms import VANILLA_ROMS
from golf.rendering.rangefinder import METADATA

from ..auth import current_user
from ..dailies import card_for, daily_seed
from ..pages import ContentPage, PageCatalog
from ..static_files import FAVICON, REVALIDATE, STATIC_DIR
from ..strings import Strings

#: the catalog prefix whose strings the ROM setup page embeds for rom.js
ROM_SCRIPT_STRINGS = "rom.status"
#: the catalog prefix whose strings the rangefinder page embeds for its modules
RANGEFINDER_SCRIPT_STRINGS = "rangefinder.script"
#: where the rangefinder's renders (`Config.rangefinder_dir`) are served
RANGEFINDER_DATA_URL = "/rangefinder-data"
#: the collection whose newest entry the home page names, when it has a summary (ADR 0031)
LATEST_UPDATE_PAGE = "updates"


def site_router(templates: Jinja2Templates, pages: PageCatalog) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request):
        updates = pages.get(LATEST_UPDATE_PAGE)
        latest = updates.newest if updates is not None else None
        db = request.app.state.db
        daily = daily_seed(db, request.app.state.clock())
        user = current_user(request)
        # the viewer's own round on it, which spoils nothing for them
        card = (
            None if daily is None or user is None else card_for(db, daily.day, user.id)
        )
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "page": "home",
                "latest_update_page": LATEST_UPDATE_PAGE,
                "latest_update": latest if latest and latest.summary else None,
                "daily": daily,
                "daily_card": card,
            },
        )

    def content_page(content: ContentPage):
        def show(request: Request):
            return templates.TemplateResponse(
                request,
                "page.html",
                {"page": "content", "content_page": content},
            )

        return show

    # A route per page rather than one `/pages/{slug}`, so each page's timings are its
    # own; a slug that names no enabled page matches nothing and is not found.
    for content in pages.enabled:
        router.add_api_route(
            f"/pages/{content.slug}",
            content_page(content),
            response_class=HTMLResponse,
            name=f"content_page_{content.slug}",
        )

    @router.get("/rom", response_class=HTMLResponse)
    def rom_setup(request: Request):
        strings: Strings = request.app.state.strings
        return templates.TemplateResponse(
            request,
            "rom.html",
            {
                "page": "rom",
                "roms": VANILLA_ROMS,
                "rom_strings": strings.for_script(ROM_SCRIPT_STRINGS),
            },
        )

    @router.get("/rangefinder", response_class=HTMLResponse)
    def rangefinder(request: Request):
        strings: Strings = request.app.state.strings
        return templates.TemplateResponse(
            request,
            "rangefinder.html",
            {
                "page": "rangefinder",
                "metadata_url": f"{RANGEFINDER_DATA_URL}/{METADATA}",
                "rangefinder_strings": strings.for_script(RANGEFINDER_SCRIPT_STRINGS),
            },
        )

    # Pages link the icon by its versioned static URL. This is for what cannot: a browser
    # showing a manifest or an image, and a crawler that asks without reading the page.
    @router.get("/favicon.ico")
    def favicon():
        return FileResponse(
            STATIC_DIR / FAVICON,
            media_type="image/x-icon",
            headers={"Cache-Control": REVALIDATE},
        )

    # UptimeRobot's free plan checks with HEAD, which a GET route would refuse with a 405.
    @router.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz(request: Request) -> dict[str, str]:
        with request.app.state.db.transaction() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    return router
