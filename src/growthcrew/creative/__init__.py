"""Visual creative: template-rendered ads in three sizes, landing page heroes, a vision critic.

Templates keep layouts on-brand; the model only fills slots (headline, sub-copy, call to
action, alt text) and picks a text scale. Rendering makes no network calls: images come from
files a person added under `workspaces/<brand>/brand/`. Contrast, text size, clipped text, the
share of the image covered by text and alt text are measured in code, and a failed check caps
the vision critic's score, so the model cannot pass an image the checks fail.
"""
