import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from competition.models import CompetitionFact, GoldenProject, PastTeam, RubricCriterion


class Command(BaseCommand):
    help = "استيراد بيانات تحدي علوم المستقبل (الموسم السادس) من ملف JSON؛ آمن للتكرار."

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(__file__).resolve().parents[2] / "data" / "season6.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        for section, key, value, source in d["facts"]:
            CompetitionFact.objects.update_or_create(
                season=6, section=section, key=key, defaults={"value": value, "source": source})
        for cat, rows in d["rubric"].items():
            for i, (name, score) in enumerate(rows, 1):
                RubricCriterion.objects.update_or_create(
                    category=cat, position=i, defaults={"name": name, "max_score": score})
        for n, ar, en, ch in d["golden"]:
            GoldenProject.objects.update_or_create(
                number=n, defaults={"name_ar": ar, "name_en": en, "core_challenge": ch})
        for no, (year, teams) in d["past_teams"].items():
            for t in teams:
                vid, title = d["videos_s5"].get(t, ["", ""]) if no == "5" else ["", ""]
                PastTeam.objects.update_or_create(
                    season_no=int(no), team_name=t,
                    defaults={"season_year": year, "video_id": vid, "video_title": title})
        self.stdout.write(
            f"facts={CompetitionFact.objects.count()} rubric={RubricCriterion.objects.count()} "
            f"golden={GoldenProject.objects.count()} teams={PastTeam.objects.count()}")
