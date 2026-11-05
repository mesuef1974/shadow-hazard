from django.contrib import admin

from .models import CompetitionFact, GoldenProject, PastTeam, RubricCriterion

for model in (CompetitionFact, GoldenProject, PastTeam, RubricCriterion):
    admin.site.register(model)
