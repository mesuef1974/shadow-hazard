from pathlib import Path
import mimetypes
import hashlib
from registry.models import Artifact, BrandDirection
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.core.paginator import Paginator
from workspace.models import DocumentAccess, ProjectDocumentation
import json
from token_usage.capacity import verified_document
ROOT = settings.BASE_DIR.parent / "docs"
EXTENSIONS = {".md", ".html", ".pdf", ".mmd"}

def resolve_document(name):
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or path.suffix.lower() not in EXTENSIONS or any(p.startswith(".") for p in Path(name).parts):
        raise Http404
    return path

@login_required(login_url="/login/")
def home(request):
    query = request.GET.get("q", "").strip()
    documents = []
    permitted = set(DocumentAccess.objects.filter(team_visible=True).values_list('path', flat=True))
    registered = {(a.path, a.sha256): a for a in Artifact.objects.filter(path__startswith="docs/")}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in EXTENSIONS and path.resolve().is_relative_to(ROOT.resolve()):
            name = path.relative_to(ROOT).as_posix()
            if request.user.role != 'supervisor' and name not in permitted: continue
            if any(p.startswith(".") for p in Path(name).parts): continue
            title = path.stem
            if path.suffix == ".md":
                title = next((line.lstrip("# ") for line in path.read_text(encoding="utf-8-sig").splitlines() if line.startswith("# ")), title)
            if query.casefold() not in (title + name).casefold(): continue
            documents.append({"name": name, "title": title, "kind": path.suffix[1:].upper(), "group": path.parts[len(ROOT.parts)]})
    if request.user.role == 'supervisor':
        receipts = ProjectDocumentation.objects.only('id', 'summary', 'session_key', 'created_at').order_by('-created_at', '-pk')
        if query:
            from django.db.models import Q
            receipts = receipts.filter(Q(summary__icontains=query) | Q(task_key__icontains=query))
        for receipt in receipts:
            documents.append({'name': f'__receipts__/{receipt.pk}.md',
                'title': f'إيصال #{receipt.pk} — {receipt.summary[:100]}',
                'kind': 'سجل المشروع', 'group': receipt.session_key,
                'record': receipt, 'receipt': True})
    page_obj = Paginator(documents, 10).get_page(request.GET.get("page"))
    for item in page_obj.object_list:
        if not item.get('receipt'):
            path = resolve_document(item['name'])
            item['record'] = registered.get(("docs/" + item['name'], hashlib.sha256(path.read_bytes()).hexdigest()))
    return render(request, "registry/portal.html", {"documents": page_obj.object_list, "page_obj": page_obj, "query": query, "brand": BrandDirection.objects.filter(status="approved").first()})

@login_required(login_url="/login/")
def document(request, name):
    if request.user.role != 'supervisor' and not DocumentAccess.objects.filter(path=name, team_visible=True).exists():
        raise Http404
    if name.startswith('__receipts__/'):
        if request.user.role != 'supervisor':
            raise Http404
        try:
            receipt = ProjectDocumentation.objects.get(pk=int(Path(name).stem))
        except (ValueError, ProjectDocumentation.DoesNotExist):
            raise Http404
        if not verified_document(receipt):
            raise Http404
        content = (f'إيصال #{receipt.pk}\nالجلسة: {receipt.session_key}\n'
                   f'المهمة: {receipt.task_key}\nالتاريخ: {receipt.created_at}\n\n'
                   + receipt.summary + '\n\nالأدلة:\n'
                   + json.dumps(receipt.evidence, ensure_ascii=False, indent=2))
        if request.GET.get('download') == '1':
            from django.http import HttpResponse
            response = HttpResponse(content, content_type='text/plain; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="receipt-{receipt.pk}.txt"'
            return response
        report = receipt
        supplements = ProjectDocumentation.objects.filter(project=receipt.project,
            evidence__source_receipt=receipt.pk,
            evidence__report_sections__isnull=False).order_by('-pk')
        for supplement in supplements.iterator():
            sections = supplement.evidence.get('report_sections')
            if (isinstance(sections, list) and sections
                    and all(isinstance(section, dict)
                            and isinstance(section.get('title'), str)
                            and isinstance(section.get('body'), str) for section in sections)
                    and verified_document(supplement)):
                report = supplement
                break
        return render(request, 'registry/document.html',
                      {'name': report.evidence.get('report_title', f'إيصال #{receipt.pk}'),
                       'content': content, 'report_sections': report.evidence.get('report_sections'),
                       'receipt_summary': report.summary,
                       'receipt_id': receipt.pk})
    path = resolve_document(name)
    if path.suffix.lower() == ".html" and request.GET.get("download") != "1":
        response = FileResponse(path.open("rb"), content_type="text/html; charset=utf-8")
        response["Content-Security-Policy"] = "sandbox allow-scripts"  # بلا أصل فلا يصل إلى الجلسة.
        response["X-Frame-Options"] = "SAMEORIGIN"  # صفحة المحاكاة تضمّن النموذج من المنصة نفسها.
        return response
    if request.GET.get("download") == "1" or path.suffix.lower() not in {".md", ".mmd"}:
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)
    return render(request, "registry/document.html", {"name": name, "content": path.read_text(encoding="utf-8-sig")})
