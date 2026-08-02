import logging

import httpx2 as httpx

from .auth import Identity

log = logging.getLogger(__name__)


def user_ref(identity: Identity) -> str:
    return f"user:{identity.subject}"


class AuthzError(Exception):
    def __init__(self, status: int, code: str = "", message: str = ""):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"OpenFGA {status} {code}: {message}".rstrip(": "))


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

    MAX_READ_PAGES = 10

    async def write(self, user: str, relation: str, obj: str) -> None:
        await self._mutate("writes", user, relation, obj)

    async def delete(self, user: str, relation: str, obj: str) -> None:
        await self._mutate("deletes", user, relation, obj)

    async def _mutate(self, key: str, user: str, relation: str, obj: str) -> None:
        if not self.api_url or not self.store_id:
            raise AuthzError(0, "not_configured", "OpenFGA is not configured")
        body: dict[str, object] = {
            "tuple_keys": [{"user": user, "relation": relation, "object": obj}]
        }
        payload: dict[str, object] = {key: body}
        if self.model_id:
            payload["authorization_model_id"] = self.model_id
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(
                    f"{self.api_url}/stores/{self.store_id}/write",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise AuthzError(0, "transport_error", str(exc)) from exc
        if resp.status_code == 200:
            return
        code, message = "", ""
        try:
            err = resp.json()
        except ValueError:
            err = {}
        if isinstance(err, dict):
            code = err.get("code") or ""
            message = err.get("message") or ""
        raise AuthzError(resp.status_code, code, message)

    async def read_tuples(self, obj: str, relation: str = "") -> list[dict]:
        if not self.api_url or not self.store_id:
            return []
        tuple_key: dict[str, str] = {"object": obj}
        if relation:
            tuple_key["relation"] = relation
        out: list[dict] = []
        token = ""
        for _ in range(self.MAX_READ_PAGES):
            payload: dict[str, object] = {"tuple_key": tuple_key, "page_size": 100}
            if token:
                payload["continuation_token"] = token
            try:
                async with httpx.AsyncClient(transport=self._transport) as client:
                    resp = await client.post(
                        f"{self.api_url}/stores/{self.store_id}/read",
                        json=payload,
                        headers=self._headers(),
                    )
            except httpx.HTTPError:
                return out
            if resp.status_code != 200:
                return out
            try:
                page = resp.json()
            except ValueError:
                return out
            if not isinstance(page, dict):
                return out
            for item in page.get("tuples") or []:
                keyed = item.get("key") if isinstance(item, dict) else None
                if isinstance(keyed, dict):
                    out.append(keyed)
            token = page.get("continuation_token") or ""
            if not token:
                break
        else:
            if token:
                log.warning(
                    "read_tuples truncated after %d pages for object %r: "
                    "more tuples exist but were not fetched",
                    self.MAX_READ_PAGES,
                    obj,
                )
        return out
