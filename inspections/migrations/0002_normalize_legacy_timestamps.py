from django.db import migrations


def normalize_timestamps(apps, schema_editor):
    # The former store wrote ISO strings with 'T'. Django writes a space separator.
    # Normalize typed columns so same-day ordering works across old and new runs.
    # JSON snapshots retain their original timestamps and all evidence unchanged.
    alias = schema_editor.connection.alias
    for model_name, field in [('CriteriaConfirmation', 'confirmed_at'), ('InspectionRun', 'created_at')]:
        model = apps.get_model('inspections', model_name)
        for row in model.objects.using(alias).only('pk', field).iterator():
            row.save(using=alias, update_fields=[field])


class Migration(migrations.Migration):
    dependencies = [('inspections', '0001_initial')]
    operations = [migrations.RunPython(normalize_timestamps, migrations.RunPython.noop)]
