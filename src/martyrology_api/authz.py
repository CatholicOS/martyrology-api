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
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.store_id = store_id
        self.model_id = model_id
        self._transport = transport

    async def check(self, user: str, relation: str, edition_id: str) -> bool:
        if not self.api_url or not self.store_id:
            return False
        body: dict[str, object] = {
            "tuple_key": {"user": user, "relation": relation, "object": f"edition:{edition_id}"}
        }
        if self.model_id:
            body["authorization_model_id"] = self.model_id
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(f"{self.api_url}/stores/{self.store_id}/check", json=body)
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            body = resp.json()
        except ValueError:
            return False
        return body.get("allowed") is True
