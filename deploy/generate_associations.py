"""Generate Apple and Android domain association files for one Lernium contour."""
import argparse
import json
import os
import re
from pathlib import Path


def generate(output: Path, *, host: str, apple_team_id: str, apple_bundle_id: str,
             android_package: str, android_sha256: list[str]) -> None:
    if host not in {"dev.platform.lernium.ru", "lernium.ru"}:
        raise ValueError("host must be dev.platform.lernium.ru or lernium.ru")
    if not re.fullmatch(r"[A-Z0-9]{10}", apple_team_id):
        raise ValueError("APPLE_TEAM_ID must be a 10-character Apple Team ID")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", apple_bundle_id):
        raise ValueError("APPLE_BUNDLE_ID is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.]+", android_package):
        raise ValueError("ANDROID_PACKAGE is invalid")
    if not android_sha256 or any(not re.fullmatch(r"(?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}", v) for v in android_sha256):
        raise ValueError("ANDROID_SHA256_CERT_FINGERPRINTS must contain colon-separated SHA-256 fingerprints")

    # Scope each app to the invitation, email verification and assigned-learning routes.
    paths = ["/invite", "/auth", "/learning/assignments/*"]
    apple = {"applinks": {"details": [{"appIDs": [f"{apple_team_id}.{apple_bundle_id}"],
                "components": [{"/": path} for path in paths]}]}}
    android = [{"relation": ["delegate_permission/common.handle_all_urls"],
                "target": {"namespace": "android_app", "package_name": android_package,
                           "sha256_cert_fingerprints": sorted(set(android_sha256))}}]
    output.mkdir(parents=True, exist_ok=True)
    (output / "apple-app-site-association").write_text(json.dumps(apple, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "assetlinks.json").write_text(json.dumps(android, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default=os.getenv("PUBLIC_HOST", ""))
    parser.add_argument("--apple-team-id", default=os.getenv("APPLE_TEAM_ID", ""))
    parser.add_argument("--apple-bundle-id", default=os.getenv("APPLE_BUNDLE_ID", ""))
    parser.add_argument("--android-package", default=os.getenv("ANDROID_PACKAGE", ""))
    parser.add_argument("--android-sha256", default=os.getenv("ANDROID_SHA256_CERT_FINGERPRINTS", ""),
                        help="comma-separated SHA-256 certificate fingerprints")
    args = parser.parse_args()
    generate(args.output, host=args.host, apple_team_id=args.apple_team_id,
             apple_bundle_id=args.apple_bundle_id, android_package=args.android_package,
             android_sha256=[v.strip() for v in args.android_sha256.split(",") if v.strip()])


if __name__ == "__main__":
    main()
