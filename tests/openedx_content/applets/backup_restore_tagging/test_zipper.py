"""
Unit tests for TaggingLearningPackageZipper._block_xml_with_tags.

Test names mirror the spec at
ok-Sud-Ispyt-shared/studio-export-plugin-spec.md §"Tests" §1.
"""
from __future__ import annotations
import logging
from uuid import uuid4

import pytest
from openedx_tagging.api import tag_object
from openedx_tagging.models import ObjectTag, Tag, Taxonomy

from openedx_content.applets.backup_restore_tagging.zipper import (
    TaggingLearningPackageZipper,
)


def _blank_zipper() -> TaggingLearningPackageZipper:
    """Build an instance without calling __init__ — helper tests don't need LP state."""
    return TaggingLearningPackageZipper.__new__(TaggingLearningPackageZipper)


def _make_taxonomy(slug: str, values: list[str]) -> Taxonomy:
    tax, _ = Taxonomy.objects.get_or_create(
        export_id=slug,
        defaults=dict(name=slug.title(), allow_free_text=False, allow_multiple=True),
    )
    for v in values:
        Tag.objects.get_or_create(taxonomy=tax, value=v)
    return tax


def _tag(uuid_str: str, taxonomy: Taxonomy, values: list[str]) -> None:
    tag_object(object_id=uuid_str, taxonomy=taxonomy, tags=values)


@pytest.mark.django_db
def test_no_meta_when_no_tags():
    z = _blank_zipper()
    xml = '<problem display_name="Q1"><multiplechoiceresponse/></problem>'
    out = z._block_xml_with_tags(xml, uuid4())
    assert "<meta>" not in out


@pytest.mark.django_db
def test_meta_injected_first_child():
    z = _blank_zipper()
    eid = uuid4()
    discipline = _make_taxonomy("discipline", ["civil-law"])
    _tag(str(eid), discipline, ["civil-law"])

    xml = '<problem display_name="Q1"><multiplechoiceresponse/></problem>'
    out = z._block_xml_with_tags(xml, eid)

    meta_pos = out.find("<meta>")
    resp_pos = out.find("<multiplechoiceresponse")
    assert 0 <= meta_pos < resp_pos, f"<meta> must precede <multiplechoiceresponse>; got: {out!r}"
    assert '<tag taxonomy="discipline">civil-law</tag>' in out


@pytest.mark.django_db
def test_multiple_taxonomies_grouped():
    z = _blank_zipper()
    eid = uuid4()
    discipline = _make_taxonomy("discipline", ["civil-law", "criminal-law"])
    section = _make_taxonomy("section", ["property-rights"])
    _tag(str(eid), discipline, ["civil-law", "criminal-law"])
    _tag(str(eid), section, ["property-rights"])

    xml = '<problem><multiplechoiceresponse/></problem>'
    out = z._block_xml_with_tags(xml, eid)

    # Each unique (taxonomy, value) pair → one <tag> element
    assert out.count('<tag taxonomy="discipline">civil-law</tag>') == 1
    assert out.count('<tag taxonomy="discipline">criminal-law</tag>') == 1
    assert out.count('<tag taxonomy="section">property-rights</tag>') == 1
    # Sorted by taxonomy export_id then value: discipline before section
    assert out.find("discipline") < out.find("section")


@pytest.mark.django_db
def test_existing_meta_replaced_not_duplicated():
    z = _blank_zipper()
    eid = uuid4()
    discipline = _make_taxonomy("discipline", ["new-value"])
    _tag(str(eid), discipline, ["new-value"])

    # Round-trip case: the input OLX already has a <meta> block from a
    # previous export. Plugin must replace it, not append a second one.
    xml = (
        '<problem display_name="Q1">'
        '<meta><tag taxonomy="discipline">stale-value</tag></meta>'
        '<multiplechoiceresponse/>'
        '</problem>'
    )
    out = z._block_xml_with_tags(xml, eid)

    assert out.count("<meta>") == 1
    assert "stale-value" not in out
    assert "new-value" in out


@pytest.mark.django_db
def test_xml_special_chars_escaped():
    z = _blank_zipper()
    eid = uuid4()
    # We can't put special chars in a controlled-vocabulary tag value (the
    # Tag model would slugify or reject them in some setups), but lxml's
    # text setter escapes them when serializing. To exercise the escape path
    # deterministically, we patch the queryset directly with a fake row.
    import unittest.mock as mock

    class FakeTaxonomy:
        id = 1
        export_id = "discipline"

    class FakeTag:
        taxonomy = FakeTaxonomy()
        _value = '<script>&"'

    fake_qs = mock.MagicMock()
    fake_qs.filter.return_value = fake_qs
    fake_qs.select_related.return_value = fake_qs
    fake_qs.order_by.return_value = iter([FakeTag()])

    xml = '<problem><multiplechoiceresponse/></problem>'
    with mock.patch.object(ObjectTag, "objects", fake_qs):
        out = z._block_xml_with_tags(xml, eid)

    # Raw < or & must NOT appear unescaped in the tag's text content
    assert "<script>" not in out
    # Escaped form must be present
    assert "&lt;script&gt;" in out
    assert "&amp;" in out


@pytest.mark.django_db
def test_non_problem_block_passes_through():
    z = _blank_zipper()
    eid = uuid4()
    discipline = _make_taxonomy("discipline", ["civil-law"])
    _tag(str(eid), discipline, ["civil-law"])

    # An <html> block, even tagged, must NOT receive <meta>.
    xml = '<html>Hello</html>'
    out = z._block_xml_with_tags(xml, eid)
    assert out == xml


@pytest.mark.django_db
def test_missing_taxonomy_export_id_skipped_with_warning(caplog):
    """
    Per spec, when a Taxonomy.export_id is empty (rare: freshly-created,
    unsaved slug), the plugin must skip those tags with a logged warning
    rather than crash. The Taxonomy model validates non-blank export_id at
    save time, so we mock the queryset directly to inject a row that would
    only exist transiently in-memory.
    """
    import unittest.mock as mock
    z = _blank_zipper()

    class FakeTaxNoSlug:
        id = 99
        export_id = ""    # the case under test

    class FakeTaxOK:
        id = 100
        export_id = "discipline"

    class FakeTag:
        def __init__(self, tax, value):
            self.taxonomy = tax
            self._value = value

    rows = [
        FakeTag(FakeTaxNoSlug(), "ignored-value"),
        FakeTag(FakeTaxOK(),     "civil-law"),
    ]

    fake_qs = mock.MagicMock()
    fake_qs.filter.return_value = fake_qs
    fake_qs.select_related.return_value = fake_qs
    fake_qs.order_by.return_value = iter(rows)

    xml = '<problem><multiplechoiceresponse/></problem>'
    with mock.patch.object(ObjectTag, "objects", fake_qs):
        with caplog.at_level(logging.WARNING):
            out = z._block_xml_with_tags(xml, uuid4())

    # The blank-export_id tag must be silently skipped — not in output.
    assert "ignored-value" not in out
    # The valid tag must still appear.
    assert '<tag taxonomy="discipline">civil-law</tag>' in out
    # A warning was logged mentioning export_id.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("export_id" in r.message for r in warnings), \
        f"expected a WARNING mentioning export_id; got: {[r.message for r in warnings]}"
