"""Exact, per-charter execution profiles; no implicit model or effort upgrades."""
from django.core.exceptions import ValidationError

MODELS = {'gpt-6-luna', 'gpt-6.1-sol', 'gpt-6-astra'}
EFFORTS = {'low', 'medium', 'high'}


def default_model_profiles():
    return [{'model': 'gpt-6-luna', 'thinking': 'low'}]


def validate_model_profiles(profiles):
    if not isinstance(profiles, list):
        raise ValidationError('السقف يجب أن يكون قائمة تركيبات نموذج وتفكير.')
    for profile in profiles:
        if (not isinstance(profile, dict) or set(profile) != {'model', 'thinking'}
                or not isinstance(profile['model'], str) or profile['model'] not in MODELS
                or not isinstance(profile['thinking'], str) or profile['thinking'] not in EFFORTS):
            raise ValidationError('تركيبة نموذج أو تفكير غير صالحة في سقف الميثاق.')


def model_limit_error(charter, model, thinking):
    profiles = charter.allowed_model_profiles
    try:
        validate_model_profiles(profiles)
    except ValidationError:
        return 'لم يبدأ التنفيذ: سقف نماذج الميثاق غير صالح.'
    if not profiles:
        return 'لم يبدأ التنفيذ: لا يوجد سقف نماذج معتمد لهذا الميثاق.'
    if {'model': model, 'thinking': thinking} not in profiles:
        return 'لم يبدأ التنفيذ: النموذج النهائي أو مستوى التفكير خارج سقف الميثاق المعتمد.'
    return None


def execution_pair(manifest, attempt):
    policy = manifest.get('policy_snapshot', {})
    if attempt == 1:
        profile = manifest.get('execution_profile', policy)
        return profile.get('model'), profile.get('thinking')
    return policy.get('stuck_model'), policy.get('stuck_thinking')
