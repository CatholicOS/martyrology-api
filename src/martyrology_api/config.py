import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MARTYROLOGY_", "env_file": ".env", "extra": "ignore"}

    data_path: str = "data/editions"  # os.pathsep-separated base dirs, one edition dir each
    crmedr_path: Path = Path("../crmedr")
    clbdr_path: Path = Path("../clbdr")
    restricted_editions: str = (
        "martyrologium_romanum_2004,"
        "martyrologium_romanum_2004_it_IT,"
        "martyrologium_romanum_2004_en_unofficial"
    )
    access_info_url: str = "https://github.com/CatholicOS/martyrology-api#licensing"
    manifest_path: str = ""  # deployment manifest.json; empty outside a bundle

    zitadel_issuer: str = ""
    zitadel_client_id: str = ""
    zitadel_client_secret: str = ""
    zitadel_project_id: str = ""

    # Transport-only override for the introspection endpoint. Empty = use
    # zitadel_issuer. Set when the browser-facing issuer is not reachable from
    # inside the API process: in Docker `localhost` is the container's own
    # loopback, and behind Plesk nginx terminates upstream. This is NEVER an
    # auth-posture input — `auth_enabled` still keys off zitadel_issuer alone.
    zitadel_internal_url: str = ""

    # Postgres DSN for the `martyrology` database. Empty = no database
    # configured; nothing in the API reads it yet. It exists so the
    # permission-request and notification subsystem lands as migrations
    # without a compose change. See the local-development-stack design, D9.
    database_url: str = ""

    openfga_api_url: str = ""
    openfga_store_id: str = ""
    openfga_model_id: str = ""
    openfga_api_token: str = ""

    github_token: str = ""
    public_repo: str = "CatholicOS/martyrology-api"
    private_repo: str = "CatholicOS/martyrology-texts"
    repo_data_prefix: str = "data/editions"
    local_git_root: str = ""  # when set, use LocalGitBackend rooted here

    @property
    def data_path_list(self) -> list[Path]:
        return [Path(p) for p in self.data_path.split(os.pathsep) if p]

    @property
    def restricted_set(self) -> set[str]:
        return {e.strip() for e in self.restricted_editions.split(",") if e.strip()}

    @property
    def manifest_file(self) -> Path | None:
        return Path(self.manifest_path) if self.manifest_path else None

    @property
    def auth_enabled(self) -> bool:
        return bool(self.zitadel_issuer)

    @property
    def authz_enabled(self) -> bool:
        return bool(self.openfga_api_url and self.openfga_store_id and self.openfga_api_token)
