from fastapi import FastAPI

from . import __version__
from .config import Settings
from .problems import install_problem_handlers
from .registry import Registry
from .routers import read
from .store import Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Roman Martyrology API", version=__version__)
    install_problem_handlers(app)
    registry = Registry.load(settings.crmedr_path, settings.clbdr_path)
    app.state.settings = settings
    app.state.registry = registry
    app.state.store = Store(settings.data_path_list, registry)
    app.include_router(read.router, prefix="/api/v1")
    return app
