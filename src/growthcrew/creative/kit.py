"""The brand kit as the templates use it: colours with sensible fallbacks, fonts, images."""

import base64
from dataclasses import dataclass
from pathlib import Path

from growthcrew.brain.models import Brain
from growthcrew.creative.access import contrast_ratio

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}  # fmt: skip
# Fonts are not downloaded while rendering, so a named font falls back to these stacks when it
# is not installed on the server.
SANS = "'Inter', 'Helvetica Neue', Arial, sans-serif"
SERIF = "Georgia, 'Times New Roman', serif"


@dataclass(frozen=True)
class Kit:
    primary: str
    background: str
    text: str
    accent: str
    cta_text: str
    heading_font: str
    body_font: str
    logo: str  # a data: URI, or ""
    product_image: str  # a data: URI, or ""
    product_image_name: str
    product_image_alt: str
    style_rules: tuple[str, ...]
    do_examples: tuple[str, ...]
    dont_examples: tuple[str, ...]
    from_brain: bool


def _font(name: str, fallback: str) -> str:
    clean = "".join(ch for ch in name if ch.isalnum() or ch in " -")
    return f"'{clean}', {fallback}" if clean else fallback


def image_uri(folder: Path, name: str) -> str:
    """A brand image as a data: URI. Only files inside the brand folder, of an image type."""
    if not name:
        return ""
    path = (folder / name).resolve()
    if folder.resolve() not in path.parents or path.suffix.lower() not in IMAGE_TYPES:
        raise ValueError(f"Brand images must be png, jpg or webp files in {folder}: {name}")
    if not path.exists():
        raise FileNotFoundError(f"Brand image not found: {path}")
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{IMAGE_TYPES[path.suffix.lower()]};base64,{data}"


def best_text(background: str) -> str:
    """Black or white, whichever reads better on `background`."""
    return max(("#111111", "#ffffff"), key=lambda colour: contrast_ratio(colour, background))


def load_kit(brand: Brain, root: Path) -> Kit:
    kit = brand.brand_kit
    colors = kit.colors
    background = colors.background or "#ffffff"
    text = colors.text or best_text(background)
    primary = colors.primary or "#1f3a5f"
    folder = root / brand.workspace / "brand"
    product = kit.product_images[0] if kit.product_images else None
    return Kit(
        primary=primary,
        background=background,
        text=text,
        accent=colors.accent or colors.secondary or primary,
        cta_text=best_text(primary),
        heading_font=_font(kit.heading_font, SANS),
        body_font=_font(kit.body_font, SANS),
        logo=image_uri(folder, kit.logo_file),
        product_image=image_uri(folder, product.file) if product else "",
        product_image_name=product.file if product else "",
        product_image_alt=product.alt if product else "",
        style_rules=tuple(kit.image_style_rules),
        do_examples=tuple(kit.do_examples),
        dont_examples=tuple(kit.dont_examples),
        from_brain=brand.brand_kit != type(brand.brand_kit)(),
    )


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}


def save_brand_image(workspace: str, name: str, data: bytes, root: Path) -> str:
    """Store an image a person uploaded in the brand folder. Returns the stored file name.

    The bytes must really be a png, jpg or webp (checked by their signature, not the name),
    under 5 MB; the name is reduced to safe characters.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Images must be under 5 MB")
    kind = next((ext for magic, ext in MAGIC.items() if data.startswith(magic)), None)
    if kind is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = ".webp"
    if kind is None:
        raise ValueError("Only png, jpg and webp images can be added")
    stem = "".join(ch for ch in Path(name).stem if ch.isalnum() or ch in "-_")[:60] or "image"
    folder = root / workspace / "brand"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}{kind}"
    number = 1
    while path.exists():
        number += 1
        path = folder / f"{stem}-{number}{kind}"
    path.write_bytes(data)
    return path.name
