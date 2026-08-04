import logging
from pathlib import Path as _Path

from fastapi import FastAPI

from . import __version__
from .auth import Authenticator
from .authz import Authz
from .caching import CacheHeadersMiddleware
from .config import Settings
from .manifest import load_manifest
from .models import HealthOut
from .problems import install_problem_handlers
from .registry import Registry
from .routers import admin, curation, discovery, read
from .store import Store
from .writer.github import GitHubBackend
from .writer.local import LocalGitBackend
from .writer.service import CurationService


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
        settings.zitadel_issuer,
        settings.zitadel_client_id,
        settings.zitadel_client_secret,
        settings.zitadel_project_id,
        settings.zitadel_internal_url,
    )
    app.state.authz = Authz(
        settings.openfga_api_url,
        settings.openfga_store_id,
        settings.openfga_model_id,
        settings.openfga_api_token,
    )
    # Any partial OpenFGA configuration denies every check: an empty URL or store
    # short-circuits `check_object` to False, and an empty token makes a preshared
    # OpenFGA answer 401. Warn on all three rather than the token alone, since each
    # produces the same silent, total denial.
    openfga_vars = {
        "MARTYROLOGY_OPENFGA_API_URL": settings.openfga_api_url,
        "MARTYROLOGY_OPENFGA_STORE_ID": settings.openfga_store_id,
        "MARTYROLOGY_OPENFGA_API_TOKEN": settings.openfga_api_token,
    }
    unset = [name for name, value in openfga_vars.items() if not value]
    if unset and len(unset) < len(openfga_vars):
        logging.getLogger(__name__).warning(
            "OpenFGA is partially configured — " + ", ".join(unset) + " empty. "
            "Every authorization check will be denied, so curation writes will fail "
            "and restricted texts will be redacted for every caller."
        )
    if settings.zitadel_issuer and not settings.zitadel_project_id:
        logging.getLogger(__name__).warning(
            "MARTYROLOGY_ZITADEL_ISSUER is set but MARTYROLOGY_ZITADEL_PROJECT_ID is empty; "
            "the roles claim cannot be built, so every curation write will 403 "
            "missing-role for every principal."
        )
    if settings.local_git_root:
        backend = LocalGitBackend(_Path(settings.local_git_root))
    elif settings.github_token:
        backend = GitHubBackend(settings.github_token)
    else:
        backend = None
    app.state.curation = (
        CurationService(backend, registry, settings) if backend is not None else None
    )
    app.include_router(discovery.router, prefix="/api/v1")
    app.include_router(curation.router, prefix="/api/v1")
    app.include_router(read.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")

    @app.get("/", tags=["service"])
    def service_document() -> dict:
        return {
            "name": "Roman Martyrology API",
            "version": __version__,
            "description": "Eulogies (elogia) of the Roman Martyrology, "
            "across current and historical editions",
            "links": {
                "openapi": "/openapi.json",
                "docs": "/docs",
                "editions": "/api/v1/editions",
                "elogia_catalog": "/api/v1/elogia",
                "repository": "https://github.com/CatholicOS/martyrology-api",
            },
        }

    @app.get("/healthz", tags=["service"], response_model=HealthOut)
    def healthz() -> HealthOut:
        manifest = load_manifest(settings.manifest_file)
        commits: dict[str, str | None] = {"crmedr": None, "clbdr": None, "texts": None}
        if manifest is not None:
            for key in commits:
                commits[key] = manifest.data.get(key)
        return HealthOut(
            status="ok",
            version=__version__,
            data=commits,
            editions=sorted(app.state.store.available()),
        )

    return app
