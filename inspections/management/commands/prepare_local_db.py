from django.core.management.base import BaseCommand
from inspections.database import prepare_database


class Command(BaseCommand):
    help = 'Back up and adopt the legacy SQLite database, then apply Django migrations.'

    def handle(self, *args, **options):
        backup = prepare_database(verbosity=options['verbosity'])
        if backup:
            self.stdout.write(f'기존 데이터베이스 백업: {backup}')
        self.stdout.write(self.style.SUCCESS('Django 데이터베이스 준비 완료'))
