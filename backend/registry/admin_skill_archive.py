"""Authenticated, read-only browsing of archived skill versions."""
import difflib
import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from .models import ServiceVersion, SkillFileSnapshot, Installation

BaseVersionAdmin = type(admin.site._registry[ServiceVersion])
admin.site.unregister(ServiceVersion)

@admin.register(ServiceVersion)
class SkillVersionAdmin(BaseVersionAdmin):
    @admin.display(description='ملفات النسخة')
    def archive_link(self,obj):
        return format_html('<a href="{}">الملفات والمقارنة والتنزيل</a>', reverse('admin:skill_archive',args=[obj.pk]))
    list_display = BaseVersionAdmin.list_display + ('archive_link',)
    readonly_fields = BaseVersionAdmin.readonly_fields + ('archive_link',)
    def get_urls(self):
        return [path('<uuid:version_id>/archive/',self.admin_site.admin_view(self.archive),name='skill_archive')] + super().get_urls()
    def archive(self,request,version_id):
        if not self.has_view_permission(request): raise PermissionDenied
        version=self.get_object(request,str(version_id))
        if not version or version.service.kind!='skill': raise Http404
        files=list(SkillFileSnapshot.objects.filter(version=version).select_related('blob'))
        versions=ServiceVersion.objects.filter(service=version.service).order_by('-created_at')
        if request.GET.get('download')=='1':
            if not files: raise Http404('لم يحفظ محتوى لهذه النسخة التاريخية')
            buffer=io.BytesIO();manifest=[]
            with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                for item in files:
                    name=PurePosixPath(item.path)
                    data=bytes(item.blob.content)
                    if name.is_absolute() or '..' in name.parts or '\\' in item.path or ':' in item.path: raise Http404
                    if hashlib.sha256(data).hexdigest()!=item.blob_id: raise Http404('تعذر التحقق من المحتوى')
                    row={'path':item.path,'kind':item.kind,'sha256':item.blob_id}
                    if item.kind=='file': archive.writestr('files/'+item.path,data)
                    else: row['link_target']=data.decode('utf-8')
                    manifest.append(row)
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            response=HttpResponse(buffer.getvalue(),content_type='application/zip')
            response['Content-Disposition']=f'attachment; filename="skill-{version.pk}.zip"'
            response['Cache-Control']='private, no-store'
            response['X-Content-Type-Options']='nosniff'
            return response
        comparison=None;changes=[]
        compare=request.GET.get('compare')
        if compare:
            comparison=next((v for v in versions if str(v.pk)==compare),None)
            if comparison is None: raise Http404
            old={f.path:f for f in SkillFileSnapshot.objects.filter(version=comparison).select_related('blob')}
            new={f.path:f for f in files}
            if not old or not new:
                changes=[{'path':'المقارنة غير متاحة','status':'لم يحفظ محتوى كامل لإحدى النسختين','diff':''}]
            else:
                for name in sorted(old.keys()|new.keys()):
                    a,b=old.get(name),new.get(name)
                    if a and b and (a.blob_id,a.kind)==(b.blob_id,b.kind): continue
                    row={'path':name,'status':'مضاف' if not a else ('محذوف' if not b else 'معدل'),'diff':''}
                    first=bytes(a.blob.content) if a else b'';second=bytes(b.blob.content) if b else b''
                    try:
                        if max(len(first),len(second))>200000 or b'\x00' in first+second: raise UnicodeError
                        row['diff']=''.join(difflib.unified_diff(first.decode('utf-8-sig').splitlines(True),second.decode('utf-8-sig').splitlines(True),fromfile='النسخة المختارة للمقارنة',tofile='النسخة المعروضة'))[:100000]
                    except UnicodeError: row['diff']='ملف ثنائي أو كبير؛ راجع البصمة والحجم أو نزّل النسخة.'
                    changes.append(row)
        context={**self.admin_site.each_context(request),'title':'ملفات المهارة ومقارنة النسخ','version':version,'files':files,'versions':versions,'comparison':comparison,'changes':changes,'opts':ServiceVersion._meta}
        return TemplateResponse(request,'admin/registry/skill_archive.html',context)

@admin.display(description='ملفات المهارة')
def installation_archive(self,obj):
    return format_html('<a href="{}">استعراض الملفات والنسخ</a>',reverse('admin:skill_archive',args=[obj.version_id]))
installation_admin=admin.site._registry[Installation]
installation_admin.__class__.installation_archive=installation_archive
installation_admin.list_display += ('installation_archive',)
installation_admin.readonly_fields += ('installation_archive',)
