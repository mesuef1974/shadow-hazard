"""Derived lexical index. Original records and their permissions remain authoritative."""
import hashlib
import json
import re
import unicodedata

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from workspace.models import Decision, ProjectDocumentation, ProjectMemory
from .models import KnowledgeChunk, KnowledgeSource


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def normalize(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = text.casefold().replace('ـ', '').translate(str.maketrans('أإآى٠١٢٣٤٥٦٧٨٩', 'اااي0123456789'))
    # Receipt summaries sometimes join words, scripts and IDs: dispatch29completed.
    text = re.sub(r'([\u0621-\u064a])([a-z])|([a-z])([\u0621-\u064a])',
                  lambda m: (m[1] or m[3]) + ' ' + (m[2] or m[4]), text)
    tokens = re.findall(r'[^\W\d_]+|\d+', text)
    # Expand the contracted Arabic definite article: للحساس -> الحساس.
    return [t if not t.startswith('لل') or len(t) < 4 else 'ال' + t[2:] for t in tokens]


def snapshot(origin):
    if isinstance(origin, ProjectMemory):
        return origin.key, origin.instruction, origin.updated_at, origin.enabled
    if isinstance(origin, Decision):
        return origin.title, origin.body, origin.created_at, origin.author.role == 'supervisor'
    payload = {'summary': origin.summary, 'evidence': origin.evidence}
    valid = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False)) == origin.digest
    if origin.evidence.get('status') in ('student_research_v3', 'student_research_v4') or 'final_product' in origin.evidence:
        if origin.evidence.get('status') == 'student_research_v4' or 'final_product' in origin.evidence:
            title = 'مقترح دراسة: مجسم مصغر ومحاكاة لملاحظة المشاة خلف حافلة'
            excerpt = 'نموذج مكتبي تفاعلي ومشهد محاكاة لمقارنة ملاحظة المشاة المحجوبين.'
        else:
            title = 'مقترح دراسة: ملاحظة المشاة المحجوبين في مشاهد المحاكاة'
            excerpt = 'هل يساعد الرصد من جهتين على ملاحظة المشاة المحجوبين أبكر من الرصد من جهة واحدة؟'
        return title, excerpt, origin.created_at, valid
    return origin.task_key, origin.summary, origin.created_at, valid


def lexical_terms(text):
    """Keep exact forms and add conservative Arabic inflection variants."""
    terms = set(normalize(text))
    for term in list(terms):
        if not re.fullmatch(r'[\u0621-\u064a]+', term):
            continue
        base = term
        if base.startswith(('وال', 'فال')):
            base = base[1:]
        if base.startswith('ال') and len(base) > 4:
            base = base[2:]
        terms.add(base)
        for suffix in ('هما', 'كما', 'هم', 'ها', 'نا', 'كم'):
            if base.endswith(suffix) and len(base) - len(suffix) >= 3:
                terms.add(base[:-len(suffix)])
        if base.endswith(('ان', 'ين')) and len(base) >= 5:
            terms.add(base[:-2])
    return terms


@transaction.atomic
def index_project(project, documentation_ids=()):
    """Only explicit documentation IDs are selected; evidence JSON is never indexed."""
    documents = list(ProjectDocumentation.objects.filter(project=project, pk__in=documentation_ids))
    if {d.pk for d in documents} != set(documentation_ids):
        raise ValueError('سجل توثيق غير موجود ضمن المشروع')
    origins = [('memory', row) for row in ProjectMemory.objects.filter(project=project)]
    origins += [('decision', row) for row in Decision.objects.filter(project=project).select_related('author')]
    # Refresh previously selected documents too, without broadening selection.
    selected = ProjectDocumentation.objects.filter(project=project, knowledge_source__isnull=False)
    documents = {d.pk: d for d in [*selected, *documents]}.values()
    origins += [('documentation', row) for row in documents]
    count = 0
    for kind, origin in origins:
        title, text, updated, valid = snapshot(origin)
        if not valid or not text.strip():
            KnowledgeSource.objects.filter(**{kind: origin}).update(enabled=False)
            continue
        source, _ = KnowledgeSource.objects.update_or_create(
            **{kind: origin}, defaults=dict(title=title[:200], enabled=True,
                source_digest=digest(title + '\n' + text), source_updated_at=updated),
        )
        source.chunks.all().delete()
        KnowledgeChunk.objects.bulk_create([
            KnowledgeChunk(source=source, position=i, text=part, digest=digest(part))
            for i, start in enumerate(range(0, len(text), 1050))
            if (part := text[start:start + 1200]).strip()
        ])
        count += 1
    return count


def search(user, project_id, query, limit=5):
    if not user.is_authenticated or not user.is_active or user.role not in ('student', 'supervisor'):
        raise PermissionDenied
    if len(query) > 500:
        raise ValueError('السؤال يتجاوز 500 حرف')
    terms = lexical_terms(query)
    if not terms:
        return []
    project_scope = Q(memory__project_id=project_id) | Q(documentation__project_id=project_id) | Q(decision__project_id=project_id)
    sources = KnowledgeSource.objects.filter(project_scope, enabled=True)
    if user.role != 'supervisor':
        sources = sources.filter(decision__private=False, decision__author__role='supervisor')
    sources = sources.select_related('memory', 'documentation', 'decision__author').prefetch_related('chunks')
    hits = []
    for source in sources:
        origin = source.memory or source.documentation or source.decision
        title, text, updated, valid = snapshot(origin)
        if not valid or digest(title + '\n' + text) != source.source_digest:
            continue  # Changed or withdrawn originals fail closed until refreshed.
        kind = 'memory' if source.memory_id else 'documentation' if source.documentation_id else 'decision'
        title_terms = lexical_terms(title)
        identifier = str(origin.pk)
        best = None
        for chunk in source.chunks.all():
            if digest(chunk.text) != chunk.digest:
                continue
            tokens = lexical_terms(chunk.text) | title_terms | {identifier}
            numbers = {term for term in terms if term.isdecimal()}
            alternatives = len(numbers) > 1 and bool(terms & {'ام', 'او'})
            number_match = bool(numbers & tokens) if alternatives else numbers.issubset(tokens)
            if numbers and not number_match:
                continue
            score = len(terms & tokens) + 2 * len(terms & title_terms)
            if identifier in numbers:
                score += 4
            if score and (best is None or score > best['score']):
                best = dict(source_id=source.pk, kind=kind, origin_id=origin.pk,
                    chunk_id=chunk.pk, title=title, text=chunk.text,
                    source_date=updated.isoformat(), score=score)
        if best:
            hits.append(best)
    return sorted(hits, key=lambda h: (-h['score'], h['source_id']))[:max(1, min(limit, 20))]
