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


def test_unrouted_path_is_problem_json():
    client = TestClient(make_app())
    r = client.get("/no-such-route")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("http-error")


def test_unhandled_exception_is_problem_json_with_no_leak():
    app = make_app()

    @app.get("/boom500")
    def boom500():
        raise RuntimeError("some secret internal detail")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/boom500")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Internal server error"
    assert body["type"].endswith("internal-error")
    assert "some secret internal detail" not in r.text
    assert "Traceback" not in r.text
