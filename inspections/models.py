"""Retain the original table names, IDs and JSON snapshots during migration."""
from django.db import models


class CriteriaConfirmation(models.Model):
    version = models.CharField(max_length=64, primary_key=True)
    confirmed_at = models.DateTimeField()

    class Meta:
        db_table = 'confirmations'
        verbose_name = '기준 확정 기록'

    def __str__(self):
        return self.version


class Asset(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    document = models.JSONField()

    class Meta:
        db_table = 'assets'
        verbose_name = '업로드 이미지'

    def __str__(self):
        return self.document.get('name', self.id)


class InspectionRun(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    idem_key = models.CharField(max_length=160, unique=True)
    fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField()
    document = models.JSONField()

    class Meta:
        db_table = 'runs'
        ordering = ['-created_at']
        verbose_name = '검수 실행 이력'

    def __str__(self):
        return self.id
