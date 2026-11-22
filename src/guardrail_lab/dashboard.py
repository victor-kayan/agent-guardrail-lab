"""Read-only loopback dashboard for saved experiment artifacts."""

import hashlib
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from guardrail_lab.comparison import compare_manifests, index_experiment
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
                                    "keys": sorted(indexed),
                                    "parameters": manifest["parameters"],
                                    "provider": manifest["provider"],
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
            if path in ASSETS:
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
        self.respond(405, {"error": "Dashboard is read-only"})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_POST


def serve(root: Path, port: int = 8765) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    with DashboardServer(root, port) as server:
        print(f"Dashboard: http://127.0.0.1:{server.server_port} (read-only)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
