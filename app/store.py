"""Django ORM persistence with the prototype's existing API snapshot format."""
import copy
import hashlib
from contextlib import contextmanager
from pathlib import Path

from django.db import connections
from inspections.models import Asset, CriteriaConfirmation, InspectionRun


class Store:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        default = connections.databases['default']
        if self.path == Path(default['NAME']).resolve():
            self.alias = 'default'
        else:
            # Separate runtime directories must not share data (tests/local demos).
            self.alias = 'inspection_' + hashlib.sha256(str(self.path).encode()).hexdigest()[:20]
            if self.alias not in connections.databases:
                config = copy.deepcopy(default)
                config['NAME'] = str(self.path)
                connections.databases[self.alias] = config

    def prepare(self):
        from inspections.database import prepare_database
        return prepare_database(self.alias, verbosity=0)

    @contextmanager
    def session(self):
        try:
            yield
        finally:
            # FastAPI worker threads do not emit Django's request_finished signal.
            connections[self.alias].close()

    def confirmed(self, version):
        with self.session():
            return CriteriaConfirmation.objects.using(self.alias).filter(pk=version).exists()

    def confirm(self, version, timestamp):
        with self.session():
            CriteriaConfirmation.objects.using(self.alias).get_or_create(
                version=version, defaults={'confirmed_at': timestamp})

    def save_asset(self, asset):
        with self.session():
            Asset.objects.using(self.alias).create(id=asset['id'], document=asset)

    def asset(self, asset_id):
        with self.session():
            return Asset.objects.using(self.alias).filter(pk=asset_id).values_list('document', flat=True).first()

    def reserve_run(self, run, key, fingerprint):
        with self.session():
            record, created = InspectionRun.objects.using(self.alias).get_or_create(
                idem_key=key, defaults={
                    'id': run['id'], 'fingerprint': fingerprint,
                    'created_at': run['created_at'], 'document': run,
                })
            if created:
                return None, True
            return record.document, record.fingerprint == fingerprint

    def save_run(self, run):
        with self.session():
            InspectionRun.objects.using(self.alias).filter(pk=run['id']).update(document=run)

    def run(self, run_id):
        with self.session():
            return InspectionRun.objects.using(self.alias).filter(pk=run_id).values_list('document', flat=True).first()

    def runs(self):
        with self.session():
            return list(InspectionRun.objects.using(self.alias).values_list('document', flat=True))
