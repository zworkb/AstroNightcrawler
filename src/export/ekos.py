"""Export capture sequence for EKOS/KStars.

The EKOS .esq format is undocumented and version-dependent.
This generates a best-effort XML file based on observed .esq structure.
Format may need adjustment for specific EKOS versions.
"""

from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

from src.models.project import CapturePoint, CaptureSettings, Project


def _add_text_element(parent: Element, tag: str, text: str) -> Element:
    """Add a child element with text content.

    Args:
        parent: Parent XML element.
        tag: Tag name for the new child element.
        text: Text content for the element.

    Returns:
        The newly created child element.
    """
    elem = SubElement(parent, tag)
    elem.text = text
    return elem


def _build_job(
    parent: Element, point: CapturePoint, settings: CaptureSettings
) -> Element:
    """Build a Job element for a single capture point.

    Args:
        parent: Parent XML element to attach the Job to.
        point: Capture point with coordinates.
        settings: Capture settings for exposure, gain, etc.

    Returns:
        The newly created Job element.
    """
    job = SubElement(parent, "Job")
    _add_text_element(job, "Exposure", str(settings.exposure_seconds))
    _add_text_element(job, "Count", str(settings.exposures_per_point))

    binning = SubElement(job, "Binning")
    _add_text_element(binning, "X", str(settings.binning))
    _add_text_element(binning, "Y", str(settings.binning))

    _add_text_element(job, "Gain", str(settings.gain))
    _add_text_element(job, "Offset", str(settings.offset))

    coords = SubElement(job, "Coordinates")
    _add_text_element(coords, "J2000RA", str(point.ra))
    _add_text_element(coords, "J2000DE", str(point.dec))

    return job


def export_sequence(project: Project, output_path: Path) -> None:
    """Export the project's capture points as an EKOS-compatible sequence file.

    Args:
        project: Project with capture points to export.
        output_path: Path to write the XML file.
    """
    root = Element("SequenceQueue", version="2.0")
    for point in project.capture_points:
        _build_job(root, point, project.capture_settings)
    tree = ElementTree(root)
    tree.write(str(output_path), xml_declaration=True, encoding="utf-8")
