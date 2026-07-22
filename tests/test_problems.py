from fastapi import FastAPI
from fastapi.testclient import TestClient
from martyrology_api.problems import ApiProblem, install_problem_handlers


def make_app():
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/boom")
    def boom():
        raise ApiProblem(404, "Not found", detail="no such day", type_slug="unknown-day")

    return app


def test_problem_shape():
    client = TestClient(make_app())
    r = client.get("/boom")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Not found"
    assert body["detail"] == "no such day"
    assert body["type"].endswith("unknown-day")
    assert body["status"] == 404


def test_validation_errors_are_problems():
    app = make_app()

    @app.get("/typed/{n}")
    def typed(n: int):
        return {"n": n}

    client = TestClient(app)
    r = client.get("/typed/xx")
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
