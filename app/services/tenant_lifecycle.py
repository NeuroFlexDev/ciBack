"""Version checks for reversible tenant metadata; no historical records are deleted."""
import hashlib
import json
from fastapi import HTTPException


GROUP_FIELDS = ('id', 'organization_id', 'name', 'description', 'is_archived')
DEPARTMENT_FIELDS = ('id', 'organization_id', 'name', 'parent_id', 'is_archived')
WEBHOOK_FIELDS = ('id', 'organization_id', 'url', 'events', 'enabled')


def state_token(row, fields):
    return hashlib.sha256(json.dumps({key: getattr(row, key) for key in fields},
        sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def require_state(row, fields, expected):
    require_client_version(expected)
    if expected != state_token(row, fields):
        raise HTTPException(409, {'code': 'revision_conflict',
            'message': 'Запись изменена. Сравните текущую версию с вашим черновиком и повторите действие.'})


def require_client_version(expected):
    if expected is None:
        raise HTTPException(409, {'code': 'client_refresh_required',
            'message': 'Интерфейс устарел. Обновите страницу перед изменением записи. Черновик сохранён.'})
