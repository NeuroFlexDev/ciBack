import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from generate_associations import generate


TEAM = "ABCDE12345"
FINGERPRINT = ":".join(["AB"] * 32)


@pytest.mark.parametrize("host", ["dev.platform.lernium.ru", "lernium.ru"])
def test_generated_associations_scope_routes_and_apps_to_one_contour(tmp_path, host):
    output = tmp_path / host
    generate(output, host=host, apple_team_id=TEAM, apple_bundle_id="ru.lernium.app",
             android_package="ru.lernium.app", android_sha256=[FINGERPRINT])

    apple = json.loads((output / "apple-app-site-association").read_text(encoding="utf-8"))
    android = json.loads((output / "assetlinks.json").read_text(encoding="utf-8"))
    assert apple["applinks"]["details"][0]["appIDs"] == ["ABCDE12345.ru.lernium.app"]
    assert {entry["/"] for entry in apple["applinks"]["details"][0]["components"]} == {
        "/invite", "/auth", "/learning/assignments/*"
    }
    assert android[0]["target"]["package_name"] == "ru.lernium.app"
    assert android[0]["target"]["sha256_cert_fingerprints"] == [FINGERPRINT]


@pytest.mark.parametrize("host", ["attacker.example", "www.lernium.ru", "dev.lernium.ru"])
def test_generator_rejects_unapproved_hosts(tmp_path, host):
    with pytest.raises(ValueError, match="host"):
        generate(tmp_path, host=host, apple_team_id=TEAM, apple_bundle_id="ru.lernium.app",
                 android_package="ru.lernium.app", android_sha256=[FINGERPRINT])


def test_generator_requires_real_signing_identifiers_and_fingerprint(tmp_path):
    with pytest.raises(ValueError):
        generate(tmp_path, host="lernium.ru", apple_team_id="TEAM", apple_bundle_id="ru.lernium.app",
                 android_package="ru.lernium.app", android_sha256=[FINGERPRINT])
    with pytest.raises(ValueError):
        generate(tmp_path, host="lernium.ru", apple_team_id=TEAM, apple_bundle_id="ru.lernium.app",
                 android_package="ru.lernium.app", android_sha256=["unknown"])
