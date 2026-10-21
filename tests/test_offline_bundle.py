"""Synthetic byte fixtures verify packaging, never claim a real model/GPU test."""
import importlib.util
import hashlib
import json
import io
import tarfile
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("offline_bundle", Path(__file__).parents[1] / "deploy/customer/offline_bundle.py")
bundle = importlib.util.module_from_spec(spec); spec.loader.exec_module(bundle)


def source_spec(root):
    license_bytes = b"Synthetic test license; not a model license"
    (root / "LICENSE").write_bytes(license_bytes)
    components = []
    for role in sorted(bundle.REQUIRED):
        content = ("Synthetic artifact for " + role).encode()
        image_id = None
        if role in bundle.IMAGE_ROLES:
            config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': []}, 'config': {'Labels': {'test-only': role}}}).encode()
            image_id = hashlib.sha256(config).hexdigest()
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as archive:
                for filename, payload in [(image_id + '.json', config), ('manifest.json', json.dumps([{'Config': image_id + '.json', 'RepoTags': [], 'Layers': []}]).encode())]:
                    entry = tarfile.TarInfo(filename); entry.size = len(payload)
                    archive.addfile(entry, io.BytesIO(payload))
            content = buffer.getvalue()
        (root / role).write_bytes(content)
        item = {"id": role, "version": "test-only", "roles": [role],
            "license": {"path": "LICENSE", "sha256": hashlib.sha256(license_bytes).hexdigest(), "identifier": "test-only", "redistribution_review": "synthetic test fixture"},
            "files": [{"path": role, "sha256": hashlib.sha256(content).hexdigest()}]}
        if role in bundle.IMAGE_ROLES:
            item.update(image_ref="example.test/fixture@sha256:" + "a" * 64, image_id="sha256:" + image_id)
        components.append(item)
    path = root / "spec.json"
    path.write_text(json.dumps({"schema": 1, "bundle_id": "test-only", "components": components}), encoding="utf-8")
    return path


def test_real_byte_packaging_checks_all_dependencies_and_tampering(tmp_path):
    spec = source_spec(tmp_path)
    destination = tmp_path / "bundle"
    report = bundle.assemble(spec, destination)
    assert report["bytes_verified"] and not report["hardware_or_egress_acceptance"]
    assert bundle.verify(destination, report["manifest_sha256"])["components"] == len(bundle.REQUIRED)
    with pytest.raises(FileExistsError): bundle.assemble(spec, destination)
    artifact = next((destination / "objects").iterdir())
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"): bundle.verify(destination, report["manifest_sha256"])


def test_missing_licenses_components_and_outside_paths_fail_closed(tmp_path):
    path = source_spec(tmp_path)
    data = json.loads(path.read_text())
    data["components"].pop()
    with pytest.raises(ValueError, match="Missing offline roles"): bundle.validate_manifest(data)
    data = json.loads(path.read_text())
    data["components"][0]["license"]["redistribution_review"] = ""
    with pytest.raises(ValueError, match="redistribution"): bundle.validate_manifest(data)
    with pytest.raises(ValueError): bundle.inside(tmp_path, "../outside.file")
    report = bundle.assemble(path, tmp_path / "good")
    (tmp_path / "good" / "runtime.env").write_text("should never be bundled")
    with pytest.raises(ValueError, match="unlisted"): bundle.verify(tmp_path / "good", report["manifest_sha256"])


def test_image_import_requires_trusted_digest_and_pins_existing_compose_services(tmp_path, monkeypatch):
    path = source_spec(tmp_path)
    report = bundle.assemble(path, tmp_path / "bundle")
    calls = []
    monkeypatch.setattr(bundle.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(bundle.subprocess, 'check_output', lambda args, **kwargs: json.dumps([{'Id': args[-1]}]))
    with pytest.raises(ValueError, match='trusted'): bundle.import_images(tmp_path / 'bundle', None, tmp_path / 'override.json')
    bundle.import_images(tmp_path / 'bundle', report['manifest_sha256'], tmp_path / 'override.json')
    assert len(calls) == len(bundle.IMAGE_ROLES)
    services = json.loads((tmp_path / 'override.json').read_text())['services']
    assert set(services) == {'backend', 'frontend', 'worker', 'outbox', 'migrate', 'maintenance', 'db', 'redis', 'edge'}
    assert all(item['pull_policy'] == 'never' and item['image'].startswith('sha256:') for item in services.values())
    with pytest.raises(ValueError, match='existing'): bundle.import_images(tmp_path / 'bundle', report['manifest_sha256'], tmp_path / 'override.json')


def test_image_archive_identity_is_verified_before_docker_or_bundle_creation(tmp_path):
    path = source_spec(tmp_path)
    data = json.loads(path.read_text())
    data['components'][0]['image_id'] = 'sha256:' + 'f' * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='image identity mismatch'):
        bundle.assemble(path, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_empty_license_is_not_a_delivered_license(tmp_path):
    path = source_spec(tmp_path)
    data = json.loads(path.read_text())
    (tmp_path / 'LICENSE').write_bytes(b'')
    for component in data['components']:
        component['license']['sha256'] = hashlib.sha256(b'').hexdigest()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='checksum'):
        bundle.assemble(path, tmp_path / 'bad')
