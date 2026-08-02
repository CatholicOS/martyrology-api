import httpx2 as httpx

from .auth import Identity


def user_ref(identity: Identity) -> str:
    return f"user:{identity.subject}"


class Authz:
    def __init__(
        self,
        api_url: str,
        store_id: str,
        model_id: str,
        api_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.store_id = store_id
        self.model_id = model_id
        self.api_token = api_token
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}

    async def check(self, user: str, relation: str, edition_id: str) -> bool:
        return await self.check_object(user, relation, f"edition:{edition_id}")

    async def check_object(self, user: str, relation: str, obj: str) -> bool:
        if not self.api_url or not self.store_id:
            return False
        body: dict[str, object] = {"tuple_key": {"user": user, "relation": relation, "object": obj}}
        if self.model_id:
            body["authorization_model_id"] = self.model_id
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(
                    f"{self.api_url}/stores/{self.store_id}/check",
                    json=body,
                    headers=self._headers(),
                )
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            payload = resp.json()
        except ValueError:
            return False
        return payload.get("allowed") is True
