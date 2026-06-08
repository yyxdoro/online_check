import json
from pathlib import Path

from .common import ARTIFACTS_DIR, LATEST_RESULT_FILE, cleanup_artifacts, read_json
from .feishu_client import send_feishu_image


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("screenshot", nargs="?")
    args = parser.parse_args()
    result = read_json(LATEST_RESULT_FILE, {}) or {}
    screenshot = args.screenshot or result.get("screenshot")
    if not screenshot or not Path(screenshot).exists():
        raise RuntimeError(f"截图不存在: {screenshot}")
    response = send_feishu_image(screenshot)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if str(screenshot).startswith(str(ARTIFACTS_DIR)):
        cleanup_artifacts([screenshot, LATEST_RESULT_FILE])
    print(f"已发送截图到飞书: {screenshot}")


if __name__ == "__main__":
    main()
