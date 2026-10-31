"""Selection policy only: launching Codex requires the explicitly authorized local tool."""
from .models import ModelPolicy


def recommend(category):
    policy=ModelPolicy.objects.get(category=category, enabled=True)
    return {'model':policy.model,'thinking':policy.thinking,'requires_owner_launch':True,'max_attempts':policy.max_attempts}
