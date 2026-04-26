"""
Extension of test_settings.py that uses PostgreSQL 18 as the backend.

PostgreSQL is supported in this fork (see docs/postgresql-fork.md).
This settings file is used to verify that:

    * the ``ci_collation`` ICU collation is created via the
      ``openedx_django_lib`` migration,
    * all ``MultiCollation*Field`` columns are created with the right
      per-vendor collation (``ci_collation`` / ``C``),
    * full migration + test suite passes on PostgreSQL.

Provision a local PostgreSQL 18 server with::

    psql -U postgres <<'SQL'
    CREATE ROLE test_oel_user WITH LOGIN CREATEDB PASSWORD 'test_oel_pass';
    CREATE DATABASE oel_db OWNER test_oel_user;
    SQL

Django's test runner will create/drop ``test_oel_db`` automatically as
long as the role has ``CREATEDB``.

Then run::

    DJANGO_SETTINGS_MODULE=pg_test_settings pytest
"""

from test_settings import *  # pylint: disable=wildcard-import

DATABASES = {
    "default": {
        "ENGINE": "openedx_django_lib.db.postgresql",
        "NAME": "oel_db",
        "USER": "test_oel_user",
        "PASSWORD": "test_oel_pass",
        "HOST": "127.0.0.1",
        "PORT": "5432",
    }
}
