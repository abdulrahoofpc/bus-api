from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import BusinessSettings, User


@admin.register(User)
class AppUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Role", {"fields": ("role", "phone")}),)
    list_display = ("username", "email", "role", "is_active")


admin.site.register(BusinessSettings)
