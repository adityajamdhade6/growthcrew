"""Marketing frameworks as Jinja templates under frameworks/templates/."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(framework: str, **context: object) -> str:
    return _env.get_template(f"{framework}.j2").render(**context)
