"""
Create custom PostgreSQL collations used by
``case_insensitive_char_field`` / ``case_sensitive_char_field``
(and ``MultiCollationCharField`` / ``MultiCollationTextField``).

Two collations are created, one mirroring each of the MySQL collations
that ``MultiCollationMixin`` already supports:

* ``ci_collation`` — non-deterministic ICU at level-2 strength.
  Case- and width-insensitive but accent-sensitive.
  Mirrors MySQL ``utf8mb4_unicode_ci``.

* ``cs_collation`` — deterministic libc collation using the ``C.utf8``
  locale. Sort and equality are byte-exact (matching MySQL
  ``utf8mb4_bin``), but ``ctype`` is UTF-8-aware so regex character
  classes such as ``\\w`` accept non-ASCII code points. The plain
  built-in ``"C"`` collation is byte-order too but ASCII-only for
  ``\\w``, which breaks the Unicode regex in
  ``code_field_check``.

On non-PostgreSQL backends (sqlite, mysql) this migration is a no-op:
collations come from per-vendor mappings declared on each field, and
``CREATE COLLATION`` is PostgreSQL-specific syntax.

Each initial migration that creates a table with a collated column
declares an explicit ``dependencies`` entry on this migration, so the
collations exist before the table is created. ``run_before`` was
considered but rejected: Django requires every node referenced by
``run_before`` to exist in the migration graph, which breaks when a
downstream consumer installs only some of openedx-core's apps (for
example, ``openedx_content`` without ``openedx_tagging`` /
``openedx_catalog``). Explicit dependencies on the dependent side are
silently ignored when the dependent app isn't installed, so the
minimal-install path works cleanly.

Requirements:

* PostgreSQL 12+ (non-deterministic ICU collations).
* ICU support compiled into PostgreSQL (default in modern builds).
* The ``C.utf8`` libc locale must be available on the OS hosting the
  database (standard on Linux distributions, and present in the
  official PostgreSQL Docker images). On systems where it is named
  differently (``C.UTF-8`` on some distros), adjust the locale string
  accordingly via a follow-up migration.
"""
from django.db import migrations


def create_collations(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "CREATE COLLATION IF NOT EXISTS ci_collation ("
        "provider = icu, locale = 'und-u-ks-level2', deterministic = false"
        ")"
    )
    schema_editor.execute(
        "CREATE COLLATION IF NOT EXISTS cs_collation ("
        "provider = libc, locale = 'C.utf8'"
        ")"
    )


def drop_collations(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP COLLATION IF EXISTS cs_collation")
    schema_editor.execute("DROP COLLATION IF EXISTS ci_collation")


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.RunPython(create_collations, drop_collations),
    ]
