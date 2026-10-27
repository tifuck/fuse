from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import accounts, stats
from ..security import csrf_guard
from ..sources.pool_pick import chatgpt_usage_percent, grok_usage_percent
from .deps import current_user, render, require_admin, require_user, restore_vault_keep, templates
from .models import _opencode_model_config, _reasoning_default, _reasoning_efforts
from .sources import _pool_card
from . import artifacts as artifacts_routes
from . import auth
from . import chat as chat_routes
from . import dashboard
from . import hosts as hosts_routes
from . import models as models_routes
from . import notifications as notifications_routes
from . import skills as skills_routes
from . import sources as sources_routes
from . import schedules as schedules_routes
from . import settings as settings_routes
from . import speech as speech_routes

router = APIRouter(dependencies=[Depends(csrf_guard)])
router.include_router(auth.router)
router.include_router(hosts_routes.router)
router.include_router(skills_routes.router)
router.include_router(schedules_routes.router)
router.include_router(dashboard.router)
router.include_router(models_routes.router)
router.include_router(chat_routes.router)
router.include_router(notifications_routes.router)
router.include_router(artifacts_routes.router)
router.include_router(sources_routes.router)
router.include_router(settings_routes.router)
router.include_router(speech_routes.router)

__all__ = [
    "router",
    "current_user",
    "render",
    "require_user",
    "require_admin",
    "restore_vault_keep",
    "templates",
    "_opencode_model_config",
    "_reasoning_default",
    "_reasoning_efforts",
    "_pool_card",
    "grok_usage_percent",
    "chatgpt_usage_percent",
    "accounts",
    "stats",
]
