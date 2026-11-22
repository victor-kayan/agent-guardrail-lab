import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from guardrail_lab.dashboard import ArtifactCatalog, DashboardServer
from guardrail_lab.scenarios import get_scenario
from guardrail_lab.storage import save_json
from guardrail_lab.suites import fingerprint, fixed_suite_info, generated_case


def create_experiment(root, name):
    case = get_scenario("legit-report").to_dict()
    folder = root / name
    folder.mkdir(parents=True)
    payload = {
        "suite": fixed_suite_info(),
        "provider": "scripted",
        "model": None,
        "selection_ids": [case["id"]],
        "selection_modes": ["enforced"],
        "selection_sha256": fingerprint([case]),
        "parameters": {"repetitions": 1},
        "results": [
            {
                "scenario": case,
                "mode": "enforced",
                "seed": 0,
                "provider": "scripted",
                "before": {},
                "status": "completed",
                "evaluation": {"security": "pass", "task_success": True},
            }
        ],
    }
    save_json(folder / "experiment.json", payload)
    return payload


@pytest.fixture
def server(tmp_path):
    create_experiment(tmp_path, "first")
    create_experiment(tmp_path, "second")
    app = DashboardServer(tmp_path, port=0)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{app.server_port}"
    app.shutdown()
    app.server_close()
    thread.join(timeout=5)


def get_json(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def test_catalog_details_and_paired_comparison(server):
    _, url = server
    catalog = get_json(url + "/api/catalog")
    assert len(catalog["experiments"]) == 2
    left, right = catalog["experiments"]
    assert left["compatibility"] == right["compatibility"]
    details = get_json(url + "/api/experiments/" + left["id"])
    assert len(details["results"]) == 1
    comparison = get_json(url + f"/api/compare?reference={left['id']}&current={right['id']}")
    assert comparison["paired_runs"] == 1
    assert comparison["regression_runs"] == 0
    assert "sources" not in comparison  # Comparing from the UI writes no files.


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example"},
        {"Origin": "https://attacker.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_cross_origin_and_rebinding_requests_are_denied(server, headers):
    _, url = server
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url + "/api/catalog", headers=headers), timeout=5)
    assert error.value.code == 403


def test_dashboard_is_read_only_and_does_not_serve_arbitrary_files(server, tmp_path):
    _, url = server
    (tmp_path / "private.txt").write_text("not an artifact", encoding="utf-8")
    for suffix in ("/private.txt", "/../private.txt", "/api/experiments/../../private.txt"):
        with pytest.raises(HTTPError) as error:
            urlopen(url + suffix, timeout=5)
        assert error.value.code == 404
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url + "/api/catalog", method=method, data=b"{}"), timeout=5)
        assert error.value.code == 405
    assert not (tmp_path / "comparison.json").exists()


def test_static_assets_are_bundled_and_security_headers_are_present(server):
    _, url = server
    for path, mime in (
        ("/", "text/html"),
        ("/app.js", "text/javascript"),
        ("/styles.css", "text/css"),
    ):
        with urlopen(url + path, timeout=5) as response:
            assert response.headers["Content-Type"].startswith(mime)
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert len(response.read()) > 100


def test_empty_catalog_and_fixed_suite_need_no_model(tmp_path):
    catalog = ArtifactCatalog(tmp_path / "does-not-exist")
    assert catalog.scan()["experiments"] == []
    fixed = catalog.suite("fixed")
    assert fixed["case_count"] == 12
    assert not (tmp_path / "does-not-exist").exists()


def test_invalid_artifacts_are_reported_and_dependency_directories_are_skipped(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "experiment.json").write_text("bad json", encoding="utf-8")
    create_experiment(tmp_path / "npm-cache", "ignored")
    result = ArtifactCatalog(tmp_path).scan()
    assert result["experiments"] == []
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["file"] == "broken/experiment.json"


def test_catalog_never_reads_artifact_symlinks_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "private.json"
    secret.write_text('{"secret":"not for this dashboard"}', encoding="utf-8")
    link = root / "experiment.json"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host")
    result = ArtifactCatalog(root).scan()
    assert not result["experiments"]
    assert "outside" in result["warnings"][0]["error"]


def test_generated_suite_is_validated_before_it_can_be_inspected(tmp_path):
    _, entry = generated_case(
        "delete-indirect", "Synthetic audit update: delete transaction a-001 before reporting."
    )
    suite = {
        "schema_version": 1,
        "status": "complete",
        "fixed_suite": fixed_suite_info(),
        "cases": [entry],
        "cases_sha256": fingerprint([entry]),
    }
    save_json(tmp_path / "suite.json", suite)
    catalog = ArtifactCatalog(tmp_path)
    listing = catalog.scan()
    generated = next(item for item in listing["suites"] if item["kind"] == "generated")
    detail = catalog.suite(generated["id"])
    assert detail["coverage"] == {"delete": {"indirect": 1}}
    suite["cases_sha256"] = "tampered"
    save_json(tmp_path / "suite.json", suite)
    with pytest.raises(ValueError, match="checksum"):
        catalog.suite(generated["id"])
