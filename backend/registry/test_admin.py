import hashlib
from io import StringIO
from pathlib import Path
from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from accounts.models import User
from registry import models

class AdminPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('import_project_registry', stdout=StringIO())
        call_command('inventory_python_packages', stdout=StringIO())
        cls.supervisor=User.objects.create_user(username='mesuef',role='supervisor',is_staff=True,is_superuser=True)
        cls.student=User.objects.create_user(username='saud',role='student')

    def setUp(self): self.client.force_login(self.supervisor)

    def test_all_registry_lists_and_detail_pages_render_and_remain_readonly(self):
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label!='registry': continue
            with self.subTest(model=model.__name__):
                url=reverse('admin:registry_'+model._meta.model_name+'_changelist')
                response=self.client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'id="searchbar"')
                self.assertFalse(response.context["has_add_permission"])
                obj=model.objects.first()
                self.assertNotIn(' object (',str(obj))
                detail=reverse('admin:registry_'+model._meta.model_name+'_change',args=[obj.pk])
                self.assertEqual(self.client.get(detail).status_code,200)
                self.assertEqual(self.client.post(detail,{}).status_code,403)
                self.assertEqual(self.client.post(reverse('admin:registry_'+model._meta.model_name+'_delete',args=[obj.pk])).status_code,403)

    def test_search_filter_and_arabic_labels(self):
        url=reverse('admin:registry_brandcolor_changelist')
        response=self.client.get(url,{'q':'#6259D8'})
        self.assertEqual(response.context['cl'].result_count,1)
        self.assertContains(response,'معاينة اللون')
        self.assertContains(response,'registry-swatch')
        direction=models.BrandDirection.objects.get(code='sightline')
        response=self.client.get(url,{'direction__id__exact':direction.pk})
        self.assertEqual(response.context['cl'].result_count,6)

    def test_asset_preview_authentication_hash_and_headers(self):
        obj=models.Artifact.objects.get(path='docs/brand/concept-board.png')
        url=reverse('admin:registry_artifact_preview',args=[obj.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'image/png')
        self.assertIn('sandbox',response['Content-Security-Policy'])
        self.assertEqual(hashlib.sha256(response.content).hexdigest(),obj.sha256)
        obj.sha256='0'*64;obj.save(update_fields=['sha256'])
        self.assertEqual(self.client.get(url).status_code,404)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code,302)
        # Even a staff account without superuser access cannot view registry assets.
        self.supervisor.is_superuser=False;self.supervisor.save(update_fields=['is_superuser'])
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code,302)

    def test_preview_rejects_paths_outside_brand_and_non_image_files(self):
        for file_path in ['backend/.local/secret.key','docs/brand/../../backend/.local/secret.key','docs/brand/index.html']:
            obj=models.Artifact.objects.create(path=file_path,sha256='0'*64)
            self.assertEqual(self.client.get(reverse('admin:registry_artifact_preview',args=[obj.pk])).status_code,404)

    def test_svg_preview_is_sandboxed(self):
        obj=models.Artifact.objects.get(path='docs/brand/sightline-symbol-draft.svg')
        response=self.client.get(reverse('admin:registry_artifact_preview',args=[obj.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'image/svg+xml')
        self.assertIn("default-src 'none'",response['Content-Security-Policy'])

    def test_package_inventory_refresh_does_not_duplicate_rows(self):
        before=set(models.PythonPackage.objects.values_list('name','pk'))
        call_command('inventory_python_packages', stdout=StringIO())
        self.assertEqual(before,set(models.PythonPackage.objects.values_list('name','pk')))
        self.assertTrue(models.PythonPackage.objects.filter(name='django',is_installed=True).exists())
