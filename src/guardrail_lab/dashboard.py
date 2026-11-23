"""Loopback dashboard for saved experiment artifacts."""

import asyncio
import hashlib
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from guardrail_lab.agent import doctor
from guardrail_lab.comparison import compare_manifests, index_experiment
from guardrail_lab.generation import generate_suite
from guardrail_lab.report import render_report
from guardrail_lab.runner import experiment
from guardrail_lab.scenarios import scenarios
from guardrail_lab.storage import read_json
from guardrail_lab.suites import describe_suite, fingerprint, fixed_suite_info, load_generated_suite

MAX_ARTIFACTS = 100
SKIP_DIRECTORIES = {"node_modules", "npm-cache", "promptfoo-config", "__pycache__", ".venv"}
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class ArtifactCatalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("Dashboard root must be a directory")
        self.paths: dict[str, Path] = {}
        self.lock = threading.RLock()

    def safe_file(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ValueError("Artifact is outside the configured directory or is missing")
        return resolved

    def read(self, identifier: str) -> dict:
        with self.lock:
            path = self.paths.get(identifier)
        if path is None:
            raise KeyError("Unknown artifact")
        value = read_json(self.safe_file(path))
        if not isinstance(value, dict):
            raise ValueError("Artifact must contain a JSON object")
        return value

    def suite(self, identifier: str) -> dict:
        if identifier == "fixed":
            cases, identity = scenarios(), fixed_suite_info()
        else:
            with self.lock:
                path = self.paths.get(identifier)
            if path is None or path.name != "suite.json":
                raise KeyError("Unknown suite")
            cases, identity = load_generated_suite(self.safe_file(path))
        return {**describe_suite(cases, identity), "texts": [case.to_dict() for case in cases]}

    def scan(self) -> dict:
        result = {
            "experiments": [],
            "suites": [
                {"id": "fixed", "name": "fixed-v1", "case_count": len(scenarios()), "kind": "fixed"}
            ],
            "warnings": [],
            "truncated": False,
        }
        paths = {}
        scanned = 0
        for directory, children, names in os.walk(self.root, followlinks=False):
            relative = Path(directory).relative_to(self.root)
            children[:] = sorted(
                child
                for child in children
                if child not in SKIP_DIRECTORIES
                and not child.startswith(".")
                and len(relative.parts) < 3
            )
            for name in sorted(set(names) & {"experiment.json", "suite.json"}):
                if scanned >= MAX_ARTIFACTS:
                    result["truncated"] = True
                    break
                scanned += 1
                path = Path(directory) / name
                display = path.relative_to(self.root).as_posix()
                identifier = hashlib.sha256(display.encode()).hexdigest()[:20]
                try:
                    safe = self.safe_file(path)
                    paths[identifier] = path
                    if name == "suite.json":
                        cases, identity = load_generated_suite(safe)
                        result["suites"].append(
                            {
                                "id": identifier,
                                "name": display,
                                "case_count": len(cases),
                                "kind": identity["kind"],
                            }
                        )
                    else:
                        manifest = read_json(safe)
                        rows = manifest["results"]
                        if not isinstance(rows, list):
                            raise ValueError("Experiment results must be a list")
                        compatibility = None
                        try:
                            indexed, identity = index_experiment(manifest)
                            compatibility = fingerprint(
                                {
                                    "suite": identity,
                                    "policy": manifest.get("policy"),
                                    "keys": sorted(indexed),
                                    "parameters": manifest["parameters"],
                                    "provider": "fixture"
                                    if manifest["provider"] == "scripted"
                                    else "model",
                                    "inputs": [
                                        {"scenario": row["scenario"], "before": row["before"]}
                                        for _, row in sorted(indexed.items())
                                    ],
                                }
                            )
                        except (ValueError, KeyError, TypeError, AttributeError):
                            pass
                        result["experiments"].append(
                            {
                                "id": identifier,
                                "name": display.removesuffix("/experiment.json"),
                                "model": manifest.get("model"),
                                "provider": manifest.get("provider"),
                                "suite_kind": manifest.get("suite", {}).get("kind", "legacy"),
                                "runs": len(rows),
                                "violations": sum(
                                    row["evaluation"]["security"] == "fail" for row in rows
                                ),
                                "modified": safe.stat().st_mtime,
                                "compatibility": compatibility,
                            }
                        )
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    paths.pop(identifier, None)
                    result["warnings"].append({"file": display, "error": str(exc)[:300]})
            if result["truncated"]:
                break
        result["experiments"].sort(key=lambda item: item["modified"], reverse=True)
        with self.lock:
            self.paths = paths
        return result


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int = 8765):
        self.catalog = ArtifactCatalog(root)
        self.jobs = {}
        self.job_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, *args):
        # Never log model content or query arguments.
        return

    def respond(self, status: int, value, content_type="application/json; charset=utf-8"):
        body = (
            value
            if isinstance(value, bytes)
            else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def local_request(self) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        return (
            host in hosts
            and (origin is None or origin == f"http://{host}")
            and self.headers.get("Sec-Fetch-Site") not in {"cross-site", "same-site"}
        )

    def do_GET(self):
        if not self.local_request():
            self.respond(
                HTTPStatus.FORBIDDEN, {"error": "Only same-origin loopback access is allowed"}
            )
            return
        if len(self.path) > 8192:
            self.respond(HTTPStatus.REQUEST_URI_TOO_LONG, {"error": "Request is too long"})
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path == "/api/jobs":
                with self.server.job_lock:
                    self.respond(200, list(self.server.jobs.values()))
            elif path in ASSETS:
                name, mime = ASSETS[path]
                self.respond(200, files("guardrail_lab").joinpath("web", name).read_bytes(), mime)
            elif path == "/api/catalog":
                self.respond(200, self.server.catalog.scan())
            elif path.startswith("/api/experiments/"):
                identifier = path.removeprefix("/api/experiments/")
                value = self.server.catalog.read(identifier)
                if "results" not in value:
                    raise KeyError("Unknown experiment")
                self.respond(200, value)
            elif path.startswith("/api/suites/"):
                self.respond(200, self.server.catalog.suite(path.removeprefix("/api/suites/")))
            elif path == "/api/compare":
                query = parse_qs(parsed.query)
                left, right = query.get("reference", []), query.get("current", [])
                if len(left) != 1 or len(right) != 1:
                    raise ValueError("Select a reference and another experiment")
                self.respond(
                    200,
                    compare_manifests(
                        self.server.catalog.read(left[0]), self.server.catalog.read(right[0])
                    ),
                )
            else:
                self.respond(404, {"error": "Not found"})
        except KeyError:
            self.respond(404, {"error": "Artifact not found; refresh the catalog"})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.respond(400, {"error": str(exc)[:400]})

    def do_POST(self):
        if self.path != "/api/jobs":
            self.respond(405, {"error": "Unsupported operation"})
            return
        if not self.local_request() or not self.headers.get("Content-Type", "").startswith(
            "application/json"
        ):
            self.respond(403, {"error": "Same-origin JSON requests required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("Invalid request size")
            request = json.loads(self.rfile.read(length))
            action = request.get("action")
            model = request.get("model", "")
            count = request.get("count", 6)
            repetitions = request.get("repetitions", 1)
            if (
                action not in {"generate", "evaluate"}
                or not isinstance(model, str)
                or not 1 <= len(model) <= 100
            ):
                raise ValueError("Select a valid action and local model")
            if (
                type(count) is not int
                or not 1 <= count <= 24
                or type(repetitions) is not int
                or not 1 <= repetitions <= 3
            ):
                raise ValueError("Count must be 1..24 and repetitions 1..3")
            cases, identity = scenarios(), fixed_suite_info()
            suite_id = request.get("suite_id", "fixed")
            if action == "evaluate" and suite_id != "fixed":
                with self.server.catalog.lock:
                    path = self.server.catalog.paths.get(suite_id)
                if path is None or path.name != "suite.json":
                    raise ValueError("Unknown generated suite")
                cases, identity = load_generated_suite(self.server.catalog.safe_file(path))
            with self.server.job_lock:
                if any(job["status"] == "running" for job in self.server.jobs.values()):
                    self.respond(409, {"error": "Another local job is running"})
                    return
                identifier = uuid4().hex
                job = {
                    "id": identifier,
                    "action": action,
                    "status": "running",
                    "completed": 0,
                    "total": count if action == "generate" else len(cases) * 3 * repetitions,
                    "message": "Verificando modelo local",
                }
                self.server.jobs[identifier] = job

            def worker():
                def progress(row):
                    with self.server.job_lock:
                        job["completed"] += 1
                        job["message"] = row.get("id") or row.get("scenario", {}).get("id", "")

                async def execute():
                    health = await doctor("http://127.0.0.1:11434", model)
                    if not health["available"]:
                        raise ValueError("Model is not installed in local Ollama")
                    output = self.server.catalog.root / ("ui-" + identifier)
                    if action == "generate":
                        await generate_suite(output, model=model, count=count, progress=progress)
                    else:
                        result = await experiment(
                            cases,
                            ["baseline", "prompt_only", "enforced"],
                            repetitions,
                            output,
                            "ollama",
                            model,
                            "http://127.0.0.1:11434",
                            180,
                            progress,
                            suite_info=identity,
                        )
                        render_report(result, output / "report.html")
                        if any(row["status"] != "completed" for row in result["results"]):
                            raise ValueError(
                                "Evaluation finished with incomplete cases; inspect saved evidence"
                            )
                    self.server.catalog.scan()

                try:
                    asyncio.run(execute())
                    with self.server.job_lock:
                        job.update(status="completed", message="Concluído. Atualize os resultados.")
                except Exception as exc:
                    with self.server.job_lock:
                        job.update(status="error", message=str(exc)[:400])

            threading.Thread(target=worker, daemon=True).start()
            self.respond(202, {"id": identifier})
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            self.respond(400, {"error": str(exc)[:400]})

    def do_PUT(self):
        self.respond(405, {"error": "Unsupported operation"})

    do_PATCH = do_DELETE = do_OPTIONS = do_PUT


def serve(root: Path, port: int = 8765) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    with DashboardServer(root, port) as server:
        print(f"Dashboard: http://127.0.0.1:{server.server_port} (local Ollama jobs)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
