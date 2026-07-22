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

    zitadel_issuer: str = ""
    zitadel_client_id: str = ""
    zitadel_client_secret: str = ""

    openfga_api_url: str = ""
    openfga_store_id: str = ""
    openfga_model_id: str = ""

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
    def auth_enabled(self) -> bool:
        return bool(self.zitadel_issuer)

    @property
    def authz_enabled(self) -> bool:
        return bool(self.openfga_api_url and self.openfga_store_id)
