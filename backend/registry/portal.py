from pathlib import Path
import mimetypes
import hashlib
from registry.models import Artifact, BrandDirection
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render
from workspace.models import DocumentAccess
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
            record = registered.get(("docs/" + name, hashlib.sha256(path.read_bytes()).hexdigest()))
            documents.append({"name": name, "title": title, "kind": path.suffix[1:].upper(), "group": path.parts[len(ROOT.parts)], "record": record})
    return render(request, "registry/portal.html", {"documents": documents, "query": query, "brand": BrandDirection.objects.filter(status="approved").first()})

@login_required(login_url="/login/")
def document(request, name):
    if request.user.role != 'supervisor' and not DocumentAccess.objects.filter(path=name, team_visible=True).exists():
        raise Http404
    path = resolve_document(name)
    if request.GET.get("download") == "1" or path.suffix.lower() not in {".md", ".mmd"}:
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)
    return render(request, "registry/document.html", {"name": name, "content": path.read_text(encoding="utf-8-sig")})
