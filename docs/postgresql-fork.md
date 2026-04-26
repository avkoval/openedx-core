# PostgreSQL Fork Notes

This fork of `openedx-core` adds PostgreSQL 18 as a supported database
backend, alongside the upstream-supported MySQL and SQLite.

## What the patch does

1. **`src/openedx_django_lib/fields.py`** — adds a `"postgresql"` key to the `db_collations` dicts produced by
   `case_insensitive_char_field` and `case_sensitive_char_field`:
   - case-insensitive → `"ci_collation"` (custom ICU, level-2 strength, non-deterministic — case- and width-insensitive,
     accent-sensitive). Behaviourally close to MySQL's `utf8mb4_unicode_ci`.
   - case-sensitive → `"cs_collation"` (custom libc collation, `C.utf8` locale). Byte-exact equality and sort
     (matching MySQL's `utf8mb4_bin`), with UTF-8-aware ctype so regex character classes such as `\w` accept
     non-ASCII code points. The plain built-in `"C"` collation is byte-order too but ASCII-only for `\w`, which
     breaks the Unicode regex used by `code_field_check`.

2. **`src/openedx_django_lib/`** is now a Django app (not just a library). It contains a single migration,
   `0001_create_pg_collation.py`, that issues `CREATE COLLATION IF NOT EXISTS` for both `ci_collation` and
   `cs_collation` on PostgreSQL only. On other backends it is a no-op. The migration uses `run_before` to insert
   itself ahead of every initial migration that creates a table with a collated column, so consumers do not need
   to add explicit dependencies.

3. **`src/openedx_django_lib/db/postgresql/`** — custom Django backend wrapping `django.db.backends.postgresql`.
   Its only deviation from the stock backend is to skip the secondary `_like` index (which uses
   `varchar_pattern_ops` / `text_pattern_ops`) for columns whose collation is `ci_collation`. PostgreSQL refuses
   to combine non-deterministic collations with those operator classes; on `ci_collation` columns the collation
   itself already provides case-insensitive `LIKE` and equality, so the `_like` index is not needed.

4. **`src/openedx_tagging/models/system_defined.py`** — replaces blanket `__iexact` lookups with field-aware
   lookups (`__iexact` for text fields, `__exact` for everything else). Django translates `__iexact` to
   `UPPER(col) = UPPER(value)` on PostgreSQL, which fails for non-text columns such as `UUIDField` /
   `IntegerField` because PG has no `upper(uuid)` / `upper(integer)`. MySQL silently coerces and SQLite is
   permissive, hiding the bug. Case is meaningless for UUIDs and integers anyway.

5. **All existing migrations** that serialize `db_collations` dicts (28 migration files + 3 model files) have been
   updated in-place to include the `"postgresql"` key alongside the existing `"mysql"` and `"sqlite"` keys.

## Installation

To use this fork:

1. Point `DATABASES["default"]["ENGINE"]` at the custom backend:

   ```python
   DATABASES = {
       "default": {
           "ENGINE": "openedx_django_lib.db.postgresql",
           # ... NAME / USER / PASSWORD / HOST / PORT
       }
   }
   ```

2. Add `openedx_django_lib` to `INSTALLED_APPS` (alongside `openedx_content`, `openedx_tagging`,
   `openedx_catalog`). It must be registered for the `run_before` mechanism to take effect.

   ```python
   INSTALLED_APPS = [
       # ...
       "openedx_django_lib",
       "openedx_content",
       "openedx_tagging",
       "openedx_catalog",
       # ...
   ]
   ```

For MySQL or SQLite consumers, the custom backend is not needed — keep the stock
`django.db.backends.mysql` / `sqlite3` engine. The `openedx_django_lib` app must still be in
`INSTALLED_APPS` because the migration graph references it, but on those backends the migration is a no-op.

## Migration strategy: fresh install only

The patch **rewrites historical migrations** to add the `"postgresql"` key. This is intentional and has the following
implications:

| Scenario                                 | Effect                                                                                                                                                                          |
|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Fresh install on PostgreSQL              | ✅ Tables created with correct collation.                                                                                                                                       |
| Fresh install on MySQL or SQLite         | ✅ Unchanged from upstream.                                                                                                                                                     |
| Existing MySQL/SQLite database           | ✅ Unchanged. Django will not re-run already-applied migrations, so the dict edits have zero runtime effect on these DBs.                                                       |
| Existing PostgreSQL database (pre-patch) | ❌ Not supported. Tables already created without `ci_collation` / `cs_collation` will not be retro-fitted. A separate `ALTER TABLE ... COLUMN ... COLLATE ...` data migration would be required. |

This fork is intended for projects that start fresh on PostgreSQL. Migration of pre-existing databases between backends,
or upgrade of an ad-hoc PostgreSQL deployment that pre-dated this patch, is out of scope.

## Why modify historical migrations rather than add a new one?

A new "ALTER COLUMN COLLATE" migration would only help post-fact and would still leave the historical files inconsistent
with the field helpers in `fields.py`. With the helpers and historical migrations in sync:

- `makemigrations` does not generate spurious "no changes" migrations due to drift between helper output and recorded
  migration state.
- New developers reading any single migration file see all three backends represented uniformly.
- The fork is self-contained: a clean checkout migrates correctly on any of the three backends without further setup.

## Collation choice rationale

| Helper                        | PG collation   | Why                                                                                                                                       |
|-------------------------------|----------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| `case_insensitive_char_field` | `ci_collation` | ICU `und-u-ks-level2`, non-deterministic. Closest behavioural match to `utf8mb4_unicode_ci`.                                              |
| `case_sensitive_char_field`   | `cs_collation` | libc `C.utf8`. Byte-order (matches `utf8mb4_bin`) but UTF-8-aware ctype so `\w` accepts Unicode code points (the plain `"C"` collation is ASCII-only for `\w`). |

The collations are created via SQL in `openedx_django_lib/migrations/0001_create_pg_collation.py`:

```sql
CREATE COLLATION IF NOT EXISTS ci_collation (
    provider = icu,
    locale = 'und-u-ks-level2',
    deterministic = false
);

CREATE COLLATION IF NOT EXISTS cs_collation (
    provider = libc,
    locale = 'C.utf8'
);
```

### Requirements

- **PostgreSQL 12+** for non-deterministic ICU collations. PostgreSQL 18 is the tested target.
- **ICU support compiled into PostgreSQL.** This is the default in modern PostgreSQL builds, including the
  official Docker images.
- **The `C.utf8` libc locale must be available on the host OS.** Standard on Linux distributions and present
  in the official PostgreSQL Docker images. On systems where it is named differently (`C.UTF-8` on some
  distros), adjust the locale string in the migration accordingly.

## Multilingual content (Ukrainian, Cyrillic, CJK, etc.)

Both collations preserve Unicode storage verbatim and behave correctly for equality and uniqueness on any
script. The trade-offs are the same as MySQL's `utf8mb4_bin` / `utf8mb4_unicode_ci`.

### `cs_collation` (case-sensitive code/key columns)

| Operation                                            | Behaviour                                                                                                |
|------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| Storage                                              | ✅ Verbatim UTF-8 bytes.                                                                                 |
| `=` and `UNIQUE`                                     | ✅ Byte-exact: `'код' = 'код'` true, `'Код' = 'код'` false.                                              |
| Regex `\w` (`code_field_check` and similar)          | ✅ Matches Ukrainian, Cyrillic, CJK, and all other Unicode letters.                                      |
| `ORDER BY`                                           | ⚠️  Codepoint order, *not* language-alphabetical. Same as `utf8mb4_bin`.                                  |
| `UPPER(col)` / `LOWER(col)`                          | ⚠️  ASCII only; does not case-fold Cyrillic. Same as `utf8mb4_bin`. Code/key columns generally don't need this. |

The sort-order limitation is essentially never a problem for code/key columns because they are slug-like ASCII
identifiers (`my_component_code`, `course-v1:Org+Course+Run`).

### `ci_collation` (case-insensitive display columns)

| Operation                                            | Behaviour                                                                                              |
|------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| `=`, `UNIQUE`, `LIKE`                                | ✅ Case- and width-insensitive across scripts: `'Київ' = 'київ'` true, `'Київ' = 'КИЇВ'` true.          |
| Accent / letter-distinction (Ukrainian: `е`/`є`, `и`/`й`, `г`/`ґ`) | ✅ Distinct (level-2 strength is accent-sensitive — correct Ukrainian behaviour). |
| Regex `\w`                                           | ✅ Unicode-aware.                                                                                       |
| `ORDER BY`                                           | ⚠️  ICU root locale. Reasonable cross-language order but *not* Ukrainian-alphabetical (`г` before `ґ`). |

### Localised sorting when you need it

If a particular query truly needs Ukrainian-alphabetical sorting (rather than ICU root order), override the
collation per-query — no schema change required. PostgreSQL ships an ICU locale for every language (`uk-x-icu`,
`ru-x-icu`, `de-x-icu`, etc.):

```sql
SELECT title FROM openedx_content_collection ORDER BY title COLLATE "uk-x-icu";
```

In Django, use `models.functions.Collate` (Django 5.2+) or a `RawSQL` order_by:

```python
from django.db.models.functions import Collate

Collection.objects.order_by(Collate("title", "uk-x-icu"))
```

The recommendation is: keep the column-level collations as configured by this patch (case-insensitive
uniqueness + Unicode-aware regex are the load-bearing properties), and override the collation at query time
in the rare places where localised alphabetical order genuinely matters in the UI.

## Running the test suite on PostgreSQL

```bash
psql -U postgres <<'SQL'
CREATE ROLE test_oel_user WITH LOGIN CREATEDB PASSWORD 'test_oel_pass';
CREATE DATABASE oel_db OWNER test_oel_user;
SQL

DJANGO_SETTINGS_MODULE=pg_test_settings pytest
```

Django's test runner will create/drop `test_oel_db` automatically as long as the role has `CREATEDB`. See
`pg_test_settings.py` for the database configuration.

## Branch

All changes live on the `pg18-collations` branch.
