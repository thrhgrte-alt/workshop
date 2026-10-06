"""Single place that imports Pillow so the rest of the domain can say ``need()`` and fail with one clear message."""

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = ImageDraw = None  # type: ignore


def available() -> bool:
    return Image is not None


def need() -> None:
    if Image is None:
        raise RuntimeError("Image features need Pillow (and numpy for analysis): pip install -e \".[imaging]\"")
