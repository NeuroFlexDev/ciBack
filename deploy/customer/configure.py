"""Prepare private deployment configuration without default credentials or overwriting files."""
import argparse
import base64
import os
from pathlib import Path
import re
import secrets


def write_private(path,content):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as target:target.write(content)


def configure(args):
    if not re.fullmatch(r"(?=.{4,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])",args.domain) or "." not in args.domain:
        raise ValueError("Use a DNS hostname, without scheme, port or path")
    images={key.upper()+"_IMAGE":getattr(args,key) for key in ("backend","frontend","postgres","redis","caddy")}
    if any(not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}",image) for image in images.values()):
        raise ValueError("Every image must be pinned by sha256 digest")
    root=Path(args.directory).resolve()
    if not root.is_dir():raise ValueError("Copy the customer bundle to an existing directory first")
    targets=[root/name for name in (".env","runtime.env","database.env")]
    if any(path.exists() for path in targets):raise ValueError("Configuration already exists; refusing to overwrite secrets")
    password=secrets.token_hex(24)
    runtime={"ENV":"prod","DEBUG":"false","DATABASE_URL":f"postgresql+psycopg2://lernium:{password}@db:5432/lernium",
        "JWT_SECRET":secrets.token_urlsafe(48),"DATA_ENCRYPTION_KEY":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "PUBLIC_APP_URL":"https://"+args.domain,"CORS_ORIGINS":"https://"+args.domain,
        "REGISTRATION_ENABLED":"false","CONTACT_REQUESTS_ENABLED":"false","AUTH_RATE_LIMIT_ENABLED":"true",
        "REDIS_URL":"redis://redis:6379/0","UPLOAD_DIR":"/data/uploads","SMTP_STARTTLS":"true"}
    env=lambda values:"".join(f"{key}={value}\n" for key,value in values.items())
    write_private(targets[0],env({"PUBLIC_HOST":args.domain}|images))
    write_private(targets[1],env(runtime))
    write_private(targets[2],env({"POSTGRES_USER":"lernium","POSTGRES_DB":"lernium","POSTGRES_PASSWORD":password}))
    print("Private configuration created. Configure SMTP and approved AI endpoints in runtime.env before enabling those features.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",default=".")
    parser.add_argument("--domain",required=True)
    for service in ("backend","frontend","postgres","redis","caddy"):parser.add_argument("--"+service,required=True)
    configure(parser.parse_args())
