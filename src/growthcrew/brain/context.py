"""How a brain is presented to agents."""

from growthcrew.brain.models import FIELD_PATHS, Brain

BRAIN_NOTE = (
    "The brand brain you are given tags each field as [confirmed] (checked by the business "
    "owner) or [inferred] (drafted from their website and not yet checked). Rely on confirmed "
    "fields. Treat inferred fields as working assumptions: do not state them as fact in "
    "anything customer-facing, do not build a recommendation on a low-confidence one without "
    "saying so, and treat empty fields as unknown rather than filling them in."
)


def render_brain(brain: Brain) -> str:
    lines = [f"Brand brain for {brain.business.name or brain.workspace} (v{brain.version})"]
    for path in FIELD_PATHS:
        meta = brain.fields.get(path)
        tag = "confirmed" if meta and meta.status == "confirmed" else "inferred"
        if tag == "inferred" and meta:
            tag += f", {meta.confidence} confidence"
        value = brain.get(path)
        if brain.is_empty(path):
            rendered = "(unknown)"
        elif isinstance(value, list):
            rendered = "".join(
                f"\n  - {v if isinstance(v, str) else v.model_dump_json()}" for v in value
            )
        elif isinstance(value, str):
            rendered = value
        else:
            rendered = value.model_dump_json()
        lines.append(f"{path} [{tag}]: {rendered}")
    return "\n".join(lines)
