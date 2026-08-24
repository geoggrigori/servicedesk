from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "job_title", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    fieldsets = (
        *DjangoUserAdmin.fieldsets,
        ("Service desk", {"fields": ("role", "job_title")}),
    )
    add_fieldsets = (
        *DjangoUserAdmin.add_fieldsets,
        ("Service desk", {"fields": ("role", "job_title")}),
    )
