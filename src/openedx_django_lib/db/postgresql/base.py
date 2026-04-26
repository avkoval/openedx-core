"""
Custom PostgreSQL backend for openedx-core.

Wraps ``django.db.backends.postgresql`` with a schema editor that knows
how to handle ``MultiCollation*Field`` columns whose PostgreSQL
collation is non-deterministic (``ci_collation``).

The standard Django PG schema editor unconditionally creates a second
``_like`` index using the ``varchar_pattern_ops`` / ``text_pattern_ops``
operator class for any indexed varchar/text column, so that ``LIKE``
queries can use the index. PostgreSQL refuses to combine those operator
classes with non-deterministic collations:

    nondeterministic collations are not supported for operator class
    "varchar_pattern_ops"

For columns using ``ci_collation`` we skip that secondary index.
``=`` and pattern matching at the SQL level are case-insensitive
through the collation itself (no ``LOWER(...)`` indirection needed),
and ordinary B-tree indexes still serve those queries correctly.

To use this backend, point ``DATABASES["default"]["ENGINE"]`` at
``openedx_django_lib.db.postgresql`` instead of
``django.db.backends.postgresql``.
"""
from django.db.backends.postgresql import base as pg_base
from django.db.backends.postgresql import schema as pg_schema


def _is_nondeterministic_collation(field) -> bool:
    """
    Return True if ``field`` declares a non-deterministic PostgreSQL
    collation via ``MultiCollationMixin.db_collations``.

    Currently the only such collation produced by this fork is
    ``ci_collation`` (see openedx_django_lib/migrations/0001_create_pg_collation.py).
    """
    db_collations = getattr(field, "db_collations", None)
    if not db_collations:
        return False
    return db_collations.get("postgresql") == "ci_collation"


class DatabaseSchemaEditor(pg_schema.DatabaseSchemaEditor):

    def _create_like_index_sql(self, model, field):
        if _is_nondeterministic_collation(field):
            return None
        return super()._create_like_index_sql(model, field)


class DatabaseWrapper(pg_base.DatabaseWrapper):
    SchemaEditorClass = DatabaseSchemaEditor
