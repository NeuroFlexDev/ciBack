"""Assemble and verify supplied offline artifacts. Never download or invent weights.

The manifest records bytes and declared licensing, not hardware acceptance or
permission to redistribute. Keep a trusted copy of manifest.sha256 separately.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

REQUIRED = {"backend", "frontend", "postgres", "redis", "caddy", "text_model", "embedding_model",
            "image_model", "text_runtime", "embedding_runtime", "image_runtime", "tokenizers", "ocr_rus", "ocr_eng", "fonts", "dependencies"}
IMAGE_ROLES = {"backend", "frontend", "postgres", "redis", "caddy", "text_runtime", "embedding_runtime", "image_runtime"}


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Artifact paths must be relative")
    candidate = root / relative
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root):
        raise ValueError("Symlink artifacts are not allowed")
    path = candidate.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Artifact missing or outside the supplied directory")
    return path


def validate_manifest(manifest):
    if manifest.get("schema") != 1 or not isinstance(manifest.get("bundle_id"), str) or not manifest["bundle_id"].strip():
        raise ValueError("Invalid bundle metadata")
    components = manifest.get("components", [])
    ids, roles = set(), set()
    for item in components:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", item.get("id", "")) or item["id"] in ids or not item.get("version"):
            raise ValueError("Every component needs a unique ID and version")
        ids.add(item["id"])
        selected = set(item.get("roles", []))
        if not selected or selected - REQUIRED or roles & selected:
            raise ValueError("Missing, unknown or duplicate component roles")
        roles |= selected
        if selected & IMAGE_ROLES:
            if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}", item.get("image_ref", "")) or not re.fullmatch(r"sha256:[a-f0-9]{64}", item.get("image_id", "")):
                raise ValueError("Container archives require a pinned image_ref and image_id")
            if len(item.get("files", [])) != 1:
                raise ValueError("A container component must contain exactly one docker-save archive")
        license_info = item.get("license", {})
        if not license_info.get("identifier") or not isinstance(license_info.get("redistribution_review"), str) or not license_info["redistribution_review"].strip():
            raise ValueError("Record license identifier and actual redistribution review reference")
        for artifact in [license_info] + item.get("files", []):
            if not re.fullmatch(r"[a-f0-9]{64}", artifact.get("sha256", "")) or not artifact.get("path"):
                raise ValueError("Every file and license needs a pinned SHA-256 and path")
        if not item.get("files"):
            raise ValueError("An empty component is not an offline artifact")
    if roles != REQUIRED:
        raise ValueError("Missing offline roles: " + ", ".join(sorted(REQUIRED - roles)))


def assemble(spec_path, destination):
    spec_path = Path(spec_path).resolve()
    manifest = json.loads(spec_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    # Check all source bytes before creating anything. Directory copying and
    # implicit globbing could accidentally include secrets or omit dependencies.
    sources = []
    for item in manifest["components"]:
        for artifact in [item["license"]] + item["files"]:
            source = inside(spec_path.parent, artifact["path"])
            if digest(source) != artifact["sha256"]:
                raise ValueError("Source checksum mismatch: " + artifact["path"])
            sources.append((artifact, source))
    destination = Path(destination).resolve()
    destination.mkdir(exist_ok=False)
    (destination / "objects").mkdir()
    for artifact, source in sources:
        relative = "objects/" + artifact["sha256"]
        target = destination / relative
        if not target.exists():
            shutil.copyfile(source, target)
        artifact["path"], artifact["size_bytes"] = relative, target.stat().st_size
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode()
    (destination / "manifest.json").write_bytes(content)
    (destination / "manifest.sha256").write_text(hashlib.sha256(content).hexdigest() + "\n", encoding="ascii")
    return verify(destination)


def verify(directory, expected_manifest=None):
    root = Path(directory).resolve()
    manifest_path = inside(root, "manifest.json")
    actual = digest(manifest_path)
    trusted = expected_manifest or inside(root, "manifest.sha256").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", trusted) or actual != trusted:
        raise ValueError("Manifest digest mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    expected = {"manifest.json", "manifest.sha256"}
    for item in manifest["components"]:
        for artifact in [item["license"]] + item["files"]:
            file = inside(root, artifact["path"])
            if file.stat().st_size != artifact["size_bytes"] or digest(file) != artifact["sha256"]:
                raise ValueError("Bundle checksum mismatch: " + artifact["path"])
            expected.add(artifact["path"])
    actual_files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual_files != expected:
        raise ValueError("Bundle contains unlisted files")
    return {"manifest_sha256": actual, "components": len(manifest["components"]), "files": len(expected) - 2,
        "bytes_verified": True, "hardware_or_egress_acceptance": False}


def import_images(directory, expected_manifest, override_path):
    # Import only after out-of-band manifest verification. Docker remains the
    # parser of its archive format; this tool never extracts arbitrary tar paths.
    if not expected_manifest:
        raise ValueError("Supply the trusted manifest digest for image import")
    root = Path(directory).resolve()
    result = verify(root, expected_manifest)
    destination = Path(override_path).resolve()
    if destination.exists():
        raise ValueError("Refusing to replace an existing Compose override")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    images = {}
    for item in manifest["components"]:
        if set(item["roles"]) & IMAGE_ROLES:
            subprocess.run(["docker", "load", "--input", str(inside(root, item["files"][0]["path"]))], check=True)
            loaded = json.loads(subprocess.check_output(["docker", "image", "inspect", item["image_id"]], text=True))
            if len(loaded) != 1 or loaded[0]["Id"] != item["image_id"]:
                raise ValueError("Imported image identity mismatch")
            images.update({role: item["image_id"] for role in set(item["roles"]) & IMAGE_ROLES})
    # Docker save/load can lose repository digests. Exact local image IDs avoid
    # a hidden pull during offline startup. Provider runtimes remain explicitly
    # configured by the operator for their model/GPU; no guessed launch command.
    mapping = {"backend": "backend", "frontend": "frontend", "db": "postgres", "redis": "redis", "edge": "caddy",
        "migrate": "backend", "worker": "backend", "outbox": "backend", "maintenance": "backend"}
    services = {service: {"image": images[role], "pull_policy": "never"} for service, role in mapping.items()}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump({"services": services}, stream, indent=2)
    return result | {"compose_override": str(destination), "model_runtimes": {role: images[role] for role in ("text_runtime", "embedding_runtime", "image_runtime")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("assemble"); create.add_argument("--spec", required=True); create.add_argument("--destination", required=True)
    for command in ("verify", "import-images"):
        child = sub.add_parser(command); child.add_argument("--directory", required=True); child.add_argument("--manifest-sha256", required=command == "import-images")
        if command == "import-images": child.add_argument("--compose-override", required=True)
    args = parser.parse_args()
    if args.command == "assemble": result = assemble(args.spec, args.destination)
    elif args.command == "verify": result = verify(args.directory, args.manifest_sha256)
    else: result = import_images(args.directory, args.manifest_sha256, args.compose_override)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
