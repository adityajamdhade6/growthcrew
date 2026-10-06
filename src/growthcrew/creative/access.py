"""Accessibility and platform checks, measured in code on the rendered creative."""

import re

from pydantic import BaseModel

# WCAG 2.x: normal text needs 4.5:1; large text (the headline) 3:1.
NORMAL_CONTRAST = 4.5
LARGE_CONTRAST = 3.0
# On a 1080px-wide image shown about 360px wide on a phone, these are roughly 11pt and 19pt.
MIN_BODY_PX = 32
MIN_HEADLINE_PX = 56
# Platforms penalise or reject image ads that are mostly text.
MAX_TEXT_SHARE = 0.35
MAX_WORDS = 30
ALT_MAX = 125


class Finding(BaseModel):
    check: str
    # Which vision-critic score a failure caps: readability, platform_rules, or none (blocks).
    caps: str
    message: str


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def alt_text_problem(alt: str, file_name: str = "") -> str:
    """Why `alt` is not usable alt text, or "" when it is fine."""
    text = alt.strip()
    if not text:
        return "The image has no alt text"
    if len(text) > ALT_MAX:
        return f"Alt text is {len(text)} characters; keep it under {ALT_MAX}"
    lowered = text.lower()
    stem = re.sub(r"\.\w+$", "", file_name).lower()
    names_file = (file_name and file_name.lower() in lowered) or (stem and lowered == stem)
    camera_name = re.search(r"\b(img|dsc|dcim|image|photo)[-_ ]?\d{2,}", lowered)
    if lowered in {"image", "photo", "picture", "logo", "img"} or names_file or camera_name:
        return "Alt text names the file or says 'image' instead of describing it"
    if len(text.split()) < 3:
        return "Alt text is too short to describe the image"
    return ""


def check_render(measure: dict, colours: dict[str, tuple[str, str]], size: str) -> list[Finding]:
    """Checks on one rendered size.

    `measure` comes from the page: per slot its font size, whether it is clipped, whether it
    leaves the safe area, plus the share of the image covered by text and the word count.
    `colours` maps a slot to its (text, background) colours.
    """
    found: list[Finding] = []
    for slot, (fg, bg) in colours.items():
        ratio = contrast_ratio(fg, bg)
        needed = LARGE_CONTRAST if slot == "headline" else NORMAL_CONTRAST
        if ratio < needed:
            message = f"{slot}: contrast {ratio:.1f}:1, needs {needed:g}:1"
            found.append(Finding(check="contrast", caps="readability", message=message))
    for slot, info in measure.get("slots", {}).items():
        minimum = MIN_HEADLINE_PX if slot == "headline" else MIN_BODY_PX
        if info.get("font_px", minimum) < minimum:
            found.append(Finding(check="text_size", caps="readability",
                                 message=f"{slot}: {info['font_px']:.0f}px text, needs "
                                 f"{minimum}px at this size"))  # fmt: skip
        if info.get("clipped"):
            found.append(Finding(check="clipped", caps="readability",
                                 message=f"{slot}: text does not fit and is cut off"))  # fmt: skip
        if info.get("outside_safe_area"):
            found.append(Finding(check="safe_area", caps="platform_rules",
                                 message=f"{slot}: sits where the platform's buttons cover it "
                                 f"in {size}"))  # fmt: skip
    share = measure.get("text_share", 0.0)
    if share > MAX_TEXT_SHARE:
        found.append(Finding(check="text_share", caps="platform_rules",
                             message=f"Text covers {share:.0%} of the image; keep it under "
                             f"{MAX_TEXT_SHARE:.0%}"))  # fmt: skip
    words = measure.get("words", 0)
    if words > MAX_WORDS:
        message = f"{words} words on the image; keep it to {MAX_WORDS}"
        found.append(Finding(check="word_count", caps="platform_rules", message=message))
    return found
