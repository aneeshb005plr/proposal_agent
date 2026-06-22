# app/documents/image_filter.py
#
# Decides whether an extracted image is likely content-bearing
# (worth a vision call) or decorative (logo, icon, letterhead —
# skip to avoid wasting cost on every page's repeated branding).

import hashlib

MIN_CONTENT_IMAGE_AREA_PX = 10_000   # ~100x100px — below this, treat
                                       # as icon/decoration, not a chart

# Tracks image hashes seen so far in the current document, so a
# logo repeated on every page is only ever flagged once, not 150 times.
_seen_image_hashes: set[str] = set()


def reset_seen_images() -> None:
    """Call once per document before processing, to avoid hash
    leakage between unrelated documents processed in sequence."""
    _seen_image_hashes.clear()


def should_describe_image(image_bytes: bytes, width: int, height: int) -> bool:
    """
    Returns True if this image is likely content-bearing and worth
    a vision call. Returns False for small or repeated images
    (logos, icons, letterhead graphics).
    """
    if width * height < MIN_CONTENT_IMAGE_AREA_PX:
        return False

    image_hash = hashlib.md5(image_bytes).hexdigest()
    if image_hash in _seen_image_hashes:
        return False  # already described this exact image once

    _seen_image_hashes.add(image_hash)
    return True