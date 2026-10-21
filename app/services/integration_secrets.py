import json
from cryptography.fernet import Fernet
from app.core.integration_config import integration_settings


def cipher():
    key = integration_settings.DATA_ENCRYPTION_KEY.get_secret_value()
    if not key:
        raise RuntimeError("DATA_ENCRYPTION_KEY is required for integrations and account mail")
    return Fernet(key.encode("ascii"))


def seal(value):
    return cipher().encrypt(json.dumps(value, ensure_ascii=False).encode()).decode("ascii")


def unseal(value):
    return json.loads(cipher().decrypt(value.encode("ascii")))
