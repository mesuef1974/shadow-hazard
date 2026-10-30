from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User
@admin.register(User)
class ProjectUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    fieldsets = UserAdmin.fieldsets + (("دور المشروع", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("دور المشروع", {"fields": ("role",)}),)
