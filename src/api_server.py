import asyncio
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .common import ARTIFACTS_DIR, write_json, read_json
from .online_studio_check import run_studio_check
from .registered_subscription_check import run_subscription_check
from .run_online_checks import run_daily_checks, run_full_checks

HOST = os.getenv("ONLINE_CHECK_API_HOST", "0.0.0.0")
PORT = int(os.getenv("ONLINE_CHECK_API_PORT") or os.getenv("PORT") or "8787")
API_TOKEN = os.getenv("ONLINE_CHECK_API_TOKEN", "")
jobs: dict[str, dict[str, Any]] = {}
active_job_id = ""
lock = threading.Lock()


def latest_result_file(result_type: str) -> Path | None:
    files = {
        "merged": "latest-merged-result.json",
        "latest": "latest-result.json",
        "password-login": "password-login-latest-result.json",
        "registered-account": "registered-account-latest-result.json",
        "subscription": "latest-subscription-result.json",
        "full": "latest-full-result.json",
    }
    name = files.get(result_type)
    return ARTIFACTS_DIR / name if name else None


async def run_check(body: dict[str, Any]) -> dict[str, Any]:
    mode = body.get("mode") or "daily"
    if mode == "daily":
        return await run_daily_checks()
    if mode == "full":
        return await run_full_checks()
    if mode == "subscription":
        return await run_subscription_check()
    if mode == "password-login":
        return await run_studio_check(
            {
                "mode": mode,
                "label": "固定账号",
                "email": body.get("email") or os.getenv("ACCOUNT_EMAIL"),
                "password": body.get("password") or os.getenv("ACCOUNT_PASSWORD"),
                "notifyFeishu": body.get("notifyFeishu") is not False,
                "resultFile": str(ARTIFACTS_DIR / "password-login-latest-result.json"),
            }
        )
    if mode == "registered-account":
        return await run_studio_check(
            {
                "mode": mode,
                "label": "新注册账号",
                "notifyFeishu": body.get("notifyFeishu") is not False,
                "resultFile": str(ARTIFACTS_DIR / "registered-account-latest-result.json"),
            }
        )
    if mode == "existing-account":
        return await run_studio_check(
            {
                "mode": "registered-account",
                "label": "已有注册账号",
                "useExistingAccount": True,
                "notifyFeishu": body.get("notifyFeishu") is not False,
                "resultFile": str(ARTIFACTS_DIR / "latest-result.json"),
            }
        )
    raise RuntimeError(f"Unsupported mode: {mode}")


def start_job(body: dict[str, Any]) -> dict[str, Any]:
    global active_job_id
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "ok": False,
        "status": "running",
        "mode": body.get("mode") or "daily",
        "startedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    with lock:
        jobs[job_id] = job
        active_job_id = job_id

    def worker() -> None:
        global active_job_id
        try:
            result = asyncio.run(run_check(body))
            job.update(
                {
                    "ok": bool(result.get("ok")),
                    "status": "succeeded" if result.get("ok") else "failed",
                    "finishedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                    "result": result,
                }
            )
        except Exception as error:
            job.update(
                {
                    "ok": False,
                    "status": "failed",
                    "finishedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                    "error": str(error),
                }
            )
        finally:
            with lock:
                if active_job_id == job_id:
                    active_job_id = ""
            write_json(ARTIFACTS_DIR / "latest-api-job.json", job)

    threading.Thread(target=worker, daemon=True).start()
    return job


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status_code: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def require_auth(self) -> bool:
        if not API_TOKEN:
            return True
        if self.headers.get("Authorization", "") == f"Bearer {API_TOKEN}":
            return True
        self.send_json(401, {"ok": False, "error": "Unauthorized"})
        return False

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        text = self.rfile.read(length).decode("utf-8").strip()
        return json.loads(text) if text else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/health", "/online-check/health"):
            self.send_json(200, {"ok": True, "activeJobId": active_job_id or None})
            return
        if not self.require_auth():
            return
        if path in ("/checks/latest", "/online-check/latest"):
            result_type = (parse_qs(parsed.query).get("type") or ["merged"])[0]
            file = latest_result_file(result_type)
            if not file:
                self.send_json(400, {"ok": False, "error": f"Unsupported latest result type: {result_type}"})
                return
            result = read_json(file, None)
            if result is None:
                self.send_json(404, {"ok": False, "error": f"No result found for type: {result_type}"})
                return
            self.send_json(200, result)
            return
        if path.startswith("/checks/jobs/") or path.startswith("/online-check/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = jobs.get(job_id) or (read_json(ARTIFACTS_DIR / "latest-api-job.json", None) if job_id == "latest" else None)
            if not job:
                self.send_json(404, {"ok": False, "error": "Job not found"})
                return
            self.send_json(200, job)
            return
        self.send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self.require_auth():
            return
        if path in ("/checks/run", "/online-check/run"):
            if active_job_id:
                self.send_json(409, {"ok": False, "error": "A check job is already running", "activeJobId": active_job_id})
                return
            body = self.read_body()
            job = start_job(body)
            self.send_json(202, job)
            return
        self.send_json(404, {"ok": False, "error": "Not found"})

    def log_message(self, format: str, *args: Any) -> None:
        print(format % args)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"online_check API listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
