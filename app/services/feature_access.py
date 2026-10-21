"""Delivery flags restrict new work without removing historical customer data."""
from fastapi import HTTPException
from app.models.learning import OrganizationLearningSettings


def feature_enabled(db, organization_id, feature):
    config = db.get(OrganizationLearningSettings, organization_id) if organization_id else None
    return not config or config.features.get(feature) is not False


def require_feature(db, organization_id, feature):
    if not feature_enabled(db, organization_id, feature):
        names = {'authoring': 'Создание и редактирование курсов', 'images': 'Иллюстрации',
                 'assistant': 'ИИ-помощник', 'integrations': 'Интеграции', 'programs': 'Программы обучения',
                 'learning': 'Учебные назначения', 'ai_grading': 'ИИ-проверка'}
        raise HTTPException(403, {'code': 'feature_unavailable', 'feature': feature,
            'message': f'{names.get(feature, feature)} не включены в поставку организации. Обратитесь к администратору.'})
