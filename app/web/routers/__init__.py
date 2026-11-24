"""Domain routers, aggregated for ``create_app`` to include.

One ``APIRouter`` per domain (never one giant file) — append new routers to
``ALL_ROUTERS`` as stages add them.
"""

from app.web.routers.adopt import router as adopt_router
from app.web.routers.analysis import router as analysis_router
from app.web.routers.auth import router as auth_router
from app.web.routers.backups import router as backups_router
from app.web.routers.cleanup import router as cleanup_router
from app.web.routers.configs import router as configs_router
from app.web.routers.dynamic_ai import expansion_ai_router, radzone_router, toxiczone_router
from app.web.routers.dynamic_ai import router as dynamic_ai_router
from app.web.routers.expansion import router as expansion_router
from app.web.routers.instances import router as instances_router
from app.web.routers.jobs import router as jobs_router
from app.web.routers.map_editor import router as map_editor_router
from app.web.routers.maps import router as maps_router
from app.web.routers.mods import router as mods_router
from app.web.routers.modxml import router as modxml_router
from app.web.routers.players import router as players_router
from app.web.routers.presets import router as presets_router
from app.web.routers.quick_setup import router as quick_setup_router
from app.web.routers.scheduler import router as scheduler_router
from app.web.routers.search import router as search_router
from app.web.routers.security import router as security_router
from app.web.routers.self_update import router as self_update_router
from app.web.routers.service import router as service_router
from app.web.routers.settings import router as settings_router
from app.web.routers.setup import router as setup_router
from app.web.routers.status import router as status_router
from app.web.routers.steam import router as steam_router
from app.web.routers.types import router as types_router
from app.web.routers.update import router as update_router
from app.web.routers.users import router as users_router
from app.web.routers.xmltools import router as xmltools_router

ALL_ROUTERS = [
    auth_router,
    status_router,
    steam_router,
    service_router,
    jobs_router,
    update_router,
    self_update_router,
    mods_router,
    modxml_router,
    expansion_router,
    dynamic_ai_router,
    expansion_ai_router,
    radzone_router,
    toxiczone_router,
    players_router,
    backups_router,
    scheduler_router,
    settings_router,
    setup_router,
    adopt_router,
    security_router,
    users_router,
    instances_router,
    cleanup_router,
    configs_router,
    types_router,
    presets_router,
    quick_setup_router,
    maps_router,
    analysis_router,
    map_editor_router,
    xmltools_router,
    search_router,
]
