"""
TaggingLearningPackageZipper — overrides block.xml emission to inject a
``<meta><tag taxonomy="X">VALUE</tag></meta>`` block as the first child of every
``<problem>`` Component, populated from ``openedx_tagging.ObjectTag`` rows
keyed on ``PublishableEntity.uuid``.

Spec: ok-Sud-Ispyt-shared/studio-export-plugin-spec.md (ADR-0006 §D1).

The override hook is ``add_file_to_zip``: it intercepts every file write,
and for ``entities/xblock.v1/problem/<slug>/component_versions/v<n>/block.xml``
paths it transforms ``content`` through the injection helper. All other
writes pass through unchanged.

A ``slug -> entity_uuid`` map is built once at construction time so the
helper can identify which entity each block.xml belongs to without
re-querying the DB on every file write.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Union
from uuid import UUID
import zipfile

from lxml import etree

from openedx_content.applets.backup_restore.zipper import LearningPackageZipper

log = logging.getLogger(__name__)


class TaggingLearningPackageZipper(LearningPackageZipper):
    """
    Subclass of :class:`LearningPackageZipper` that injects ``<meta>`` tag
    blocks into exported ``<problem>`` block.xml files.
    """

    def __init__(self, learning_package, user, origin_server) -> None:
        super().__init__(learning_package, user, origin_server)
        # We need a slug -> entity_uuid lookup so add_file_to_zip can identify
        # which problem each block.xml belongs to. But the parent's
        # get_entity_toml_filename is *stateful* — calling it twice for the
        # same input returns a different (hashed) slug the second time. So we
        # cannot pre-call it here. Instead: build a code -> uuid map now, then
        # override get_entity_toml_filename to passively populate slug -> uuid
        # as the parent computes slugs during create_zip().
        self._problem_uuid_by_code: dict[str, UUID] = {}
        self._problem_uuid_by_slug: dict[str, UUID] = {}
        for entity in learning_package.publishable_entities.all():
            if not hasattr(entity, "component"):
                continue
            if entity.component.component_type.name != "problem":
                continue
            self._problem_uuid_by_code[entity.component.component_code] = entity.uuid

    def get_entity_toml_filename(self, entity_ref: str) -> str:
        """Observe the slug parent computes for each ref; record problem mappings."""
        filename = super().get_entity_toml_filename(entity_ref)
        if entity_ref in self._problem_uuid_by_code:
            self._problem_uuid_by_slug[filename] = self._problem_uuid_by_code[entity_ref]
        return filename

    def add_file_to_zip(
        self,
        zip_file: zipfile.ZipFile,
        file_path: Path,
        content: Optional[Union[bytes, str]] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Override: transform block.xml of <problem> components, pass-through everything else."""
        if content is not None:
            entity_uuid = self._problem_uuid_for_path(file_path)
            if entity_uuid is not None:
                # Decode bytes for XML transformation; super() will re-encode.
                if isinstance(content, bytes):
                    text = content.decode("utf-8")
                else:
                    text = content
                content = self._block_xml_with_tags(text, entity_uuid)
        super().add_file_to_zip(zip_file, file_path, content, timestamp)

    def _problem_uuid_for_path(self, file_path: Path) -> Optional[UUID]:
        """Return the PublishableEntity.uuid if this path is a problem block.xml, else None."""
        parts = Path(file_path).parts
        # Expected shape: entities/xblock.v1/problem/<slug>/component_versions/v<n>/block.xml
        if (
            len(parts) >= 7
            and parts[0] == "entities"
            and parts[1] == "xblock.v1"
            and parts[2] == "problem"
            and parts[-1] == "block.xml"
        ):
            slug = parts[3]
            return self._problem_uuid_by_slug.get(slug)
        return None

    def _block_xml_with_tags(
        self,
        original_xml: str,
        publishable_entity_uuid: UUID,
    ) -> str:
        """
        Inject ``<meta>`` block ahead of the response element of a ``<problem>``.
        See spec §"The contract".
        """
        try:
            root = etree.fromstring(original_xml.encode("utf-8"))
        except etree.XMLSyntaxError as e:
            log.warning(
                "Could not parse block.xml for entity %s: %s",
                publishable_entity_uuid, e,
            )
            return original_xml

        # Pass-through if not a <problem>. (This is also gated by path matching
        # in the caller, but we double-check defensively.)
        if root.tag != "problem":
            return original_xml

        # Look up tags for this entity. Defer model import to runtime so this
        # module imports cleanly even before Django app registry is ready.
        from openedx_tagging.models import ObjectTag  # noqa: WPS433
        tags_qs = (
            ObjectTag.objects
            .filter(object_id=str(publishable_entity_uuid))
            .select_related("taxonomy")
            .order_by("taxonomy__export_id", "_value")
        )

        # Group by taxonomy export_id; de-duplicate (taxonomy, value) pairs.
        grouped: dict[str, list[str]] = defaultdict(list)
        for ot in tags_qs:
            export_id = ot.taxonomy.export_id
            if not export_id:
                log.warning(
                    "Skipping ObjectTag with empty export_id on taxonomy %s "
                    "for entity %s",
                    ot.taxonomy.id, publishable_entity_uuid,
                )
                continue
            value = ot._value or ""  # noqa: WPS437
            if value not in grouped[export_id]:
                grouped[export_id].append(value)

        if not grouped:
            return original_xml  # no tags -> unchanged (spec: "no empty <meta>")

        # Build the new <meta> block. lxml escapes special chars in text
        # content automatically, so we never raw-string-concat tag values.
        meta = etree.Element("meta")
        for export_id in sorted(grouped):
            for value in grouped[export_id]:
                tag_el = etree.SubElement(meta, "tag", taxonomy=export_id)
                tag_el.text = value

        # Replace any pre-existing <meta> (round-trip case), else insert at
        # position 0.
        existing = root.find("meta")
        if existing is not None:
            root.remove(existing)
        root.insert(0, meta)

        # Serialize. pretty_print is fine but not required by the importer.
        return etree.tostring(root, encoding="unicode", pretty_print=True)
