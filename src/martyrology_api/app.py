from fastapi import FastAPI

from . import __version__
from .auth import Authenticator
from .authz import Authz
from .caching import CacheHeadersMiddleware
from .config import Settings
from .problems import install_problem_handlers
from .registry import Registry
from .routers import discovery, read
from .store import Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Roman Martyrology API", version=__version__)
    app.add_middleware(CacheHeadersMiddleware)
    install_problem_handlers(app)
    registry = Registry.load(settings.crmedr_path, settings.clbdr_path)
    app.state.settings = settings
    app.state.registry = registry
    app.state.store = Store(settings.data_path_list, registry)
    app.state.authenticator = Authenticator(
        settings.zitadel_issuer, settings.zitadel_client_id,
        settings.zitadel_client_secret)
    app.state.authz = Authz(settings.openfga_api_url,
                            settings.openfga_store_id,
                            settings.openfga_model_id)
    app.include_router(discovery.router, prefix="/api/v1")
    app.include_router(read.router, prefix="/api/v1")
    return app
