"""Release tag contract.

The only tag that may publish a release is the canonical distribution tag
(``v3.0.0-pager.8``). The bare product tag (``v3.0.0``) and every other tag must
be rejected: a release has to line up with the distribution version, the
Android ``version_name``, and the pinned signer fingerprint.

``release.yml``'s verify job calls ``verify_release_tag`` so the acceptance
tests exercise the exact code the workflow runs instead of a copy of it. This
module imports only the standard library and is safe to run from a bare
Actions runner before test dependencies are installed.
"""
from __future__ import annotations

from pathlib import Path

from deploy.release_metadata import RELEASE_METADATA_FILENAME, load_release_metadata


class TagContractError(ValueError):
    """Raised when a pushed tag is not the canonical distribution tag."""


def canonical_distribution_tag(root: Path) -> str:
    """Return ``v{metadata.distribution_version}`` (e.g. ``v3.0.0-pager.8``)."""
    metadata_path = Path(root) / "deploy" / RELEASE_METADATA_FILENAME
    return f"v{load_release_metadata(metadata_path).distribution_version}"


def verify_release_tag(tag: str, root: Path) -> str:
    """Return the canonical tag if ``tag`` equals it, else raise
    :class:`TagContractError`."""
    expected = canonical_distribution_tag(root)
    if tag != expected:
        raise TagContractError(
            f"release tag {tag!r} is not the canonical distribution tag {expected!r}"
        )
    return expected
