"""
App config for openedx_django_lib.

This is a small Django app whose only purpose is to host the migration that
creates database-level collations needed by ``MultiCollationCharField`` /
``MultiCollationTextField`` fields used elsewhere in openedx-core.
"""
from django.apps import AppConfig


class OpenedxDjangoLibConfig(AppConfig):
    name = "openedx_django_lib"
    verbose_name = "Open edX Core > Django Lib"
    default_auto_field = "django.db.models.BigAutoField"
    label = "openedx_django_lib"
