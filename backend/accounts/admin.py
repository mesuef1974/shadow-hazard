from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User
@admin.register(User)
class ProjectUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("دور المشروع", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("دور المشروع", {"fields": ("role",)}),)
