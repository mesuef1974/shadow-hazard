"""يبني قائمة الكلمات من النص السليم في القاعدة والوثائق، والكلمة النادرة تُترك خارجها."""
import collections
import re

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models

from knowledge.readable import LONGEST_WORD, VOCABULARY

WORD = re.compile(r'[ء-ي]+')
MIN_COUNT = 3


def stored_texts():
    for model in apps.get_models():
        fields = [field.name for field in model._meta.fields
                  if isinstance(field, (models.CharField, models.TextField))]
        if fields:
            for row in model.objects.values_list(*fields).iterator():
                yield from (value for value in row if isinstance(value, str))


def document_texts():
    for path in (settings.BASE_DIR.parent / 'docs').rglob('*.md'):
        yield path.read_text(encoding='utf-8', errors='ignore')


class Command(BaseCommand):
    help = 'يبني knowledge/arabic_vocabulary.txt'

    def handle(self, *args, **options):
        counts = collections.Counter()
        for text in (*stored_texts(), *document_texts()):
            counts.update(word for word in WORD.findall(text)
                          if 1 < len(word) <= LONGEST_WORD or word in ('و', 'ب'))
        lines = [f'{word} {count}' for word, count in sorted(counts.items()) if count >= MIN_COUNT]
        VOCABULARY.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.stdout.write(f'{len(lines)} كلمة')
