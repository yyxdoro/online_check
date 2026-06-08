import json
import os
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ROOT / "artifacts"
LATEST_ACCOUNT_FILE = ROOT / "latest-account.json"
LATEST_RESULT_FILE = ARTIFACTS_DIR / "latest-result.json"
LATEST_MERGED_RESULT_FILE = ARTIFACTS_DIR / "latest-merged-result.json"
LATEST_SUBSCRIPTION_RESULT_FILE = ARTIFACTS_DIR / "latest-subscription-result.json"

ACCOUNTS_FILE = ROOT / "online-registered-accounts.json"

APP_URL = "https://studio.tripo3d.ai/workspace"
AUTH_BASE_URL = "https://auth.tripo3d.ai"
WEB_ORIGIN = "https://web.tripo3d.ai"
API_BASE_URL = "https://api.tripo3d.ai"
DEFAULT_PROMPT = "a cute low poly robot mascot, white background"


def load_env_file(file: Path | None = None) -> None:
    env_file = file or ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def read_json(file: Path, fallback: Any = None) -> Any:
    if not file.exists():
        return fallback
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(file: Path, value: Any) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cleanup_artifacts(paths: list[str | Path | None]) -> None:
    for item in paths:
        if not item:
            continue
        try:
            Path(item).unlink(missing_ok=True)
        except Exception:
            pass


def python_executable() -> str:
    return shutil.which("python3") or shutil.which("python") or "python3"


load_env_file()
REGISTER_DIR = Path(os.getenv("REGISTER_DIR") or ROOT.parent / "register")
REGISTER_SCRIPT = REGISTER_DIR / "index.js"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
