from django.contrib import admin
from .models import Asset, CriteriaConfirmation, InspectionRun


class SnapshotAdmin(admin.ModelAdmin):
    # Inspection snapshots are evidence; edit them only through the API workflow.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CriteriaConfirmation)
class ConfirmationAdmin(SnapshotAdmin):
    list_display = ('version', 'confirmed_at')
    search_fields = ('version',)


@admin.register(Asset)
class AssetAdmin(SnapshotAdmin):
    list_display = ('id',)
    search_fields = ('id',)


@admin.register(InspectionRun)
class RunAdmin(SnapshotAdmin):
    list_display = ('id', 'created_at', 'idem_key')
    search_fields = ('id', 'idem_key')
