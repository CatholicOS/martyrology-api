from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_TYPE_BASE = "https://romanmartyrology.com/problems/"


class ApiProblem(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None,
                 type_slug: str = "about:blank", **extensions):
        self.status = status
        self.title = title
        self.detail = detail
        self.type_slug = type_slug
        self.extensions = extensions
        super().__init__(title)


def problem_response(exc: ApiProblem) -> JSONResponse:
    body = {
        "type": exc.type_slug if exc.type_slug == "about:blank"
                else PROBLEM_TYPE_BASE + exc.type_slug,
        "title": exc.title,
        "status": exc.status,
    }
    if exc.detail:
        body["detail"] = exc.detail
    body.update(exc.extensions)
    return JSONResponse(body, status_code=exc.status,
                        media_type="application/problem+json")


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def _api_problem(request: Request, exc: ApiProblem):
        return problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return problem_response(
            ApiProblem(400, "Malformed request", detail=str(exc.errors()),
                       type_slug="malformed-request"))
