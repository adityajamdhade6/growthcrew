import pytest

from growthcrew import cli, config
from growthcrew.brain import store
from growthcrew.db import session as db_session
from test_strategy import BRAND


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Run the CLI in an empty folder with its own database."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    db_session.get_engine.cache_clear()
    yield tmp_path
    db_session.get_engine.cache_clear()


def run(*argv):
    return cli.main(list(argv))


def test_commands_that_need_a_brain_say_so(project, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["growthcrew", "show", "nobody"])
    assert cli.run() == 1
    assert "No brain found for workspace 'nobody'" in capsys.readouterr().out


def test_show_and_confirm(project, capsys):
    store.save_brain(BRAND, note="t", root=project / "workspaces", engine=db_session.get_engine())
    assert run("show", "acme") == 0
    out = capsys.readouterr().out
    assert "icp.pains  [inferred, low]" in out and "Weakest fields" in out
    assert run("confirm", "acme", "icp.pains") == 0
    assert "Saved v2 with confirmed: icp.pains" in capsys.readouterr().out


def test_model_commands_stop_cleanly_without_credentials(project, capsys):
    store.save_brain(BRAND, note="t", root=project / "workspaces", engine=db_session.get_engine())
    assert run("onboard", "--url", "https://acme.test", "--no-input") == 1
    assert run("research", "acme") == 1
    assert run("cycle", "acme") == 1
    assert capsys.readouterr().out.count("No Anthropic credentials found") == 3


def test_user_add(project, capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda: "a-long-enough-password")
    assert run("user", "add", "Me@Example.com", "--workspaces", "acme") == 0
    assert "Added me@example.com with access to: acme" in capsys.readouterr().out
    assert run("user", "add", "me@example.com") == 1  # already exists
    monkeypatch.setattr("getpass.getpass", lambda: "short")
    assert run("user", "add", "other@example.com") == 1
    assert "at least 10 characters" in capsys.readouterr().out


def test_pilot_commands(project, capsys):
    assert run("pilot", "init", "acme") == 1  # needs a start date
    assert run("pilot", "init", "acme", "--start", "2026-10-05", "--business", "Acme Bakery") == 0
    assert (project / "workspaces/acme/pilot/baseline.csv").exists()
    assert run("pilot", "track", "acme") == 0
    assert run("pilot", "report", "acme") == 1  # needs --day
    assert run("pilot", "report", "acme", "--day", "30") == 0
    out = capsys.readouterr().out
    assert "Pilot tracker: Acme Bakery" in out and "Day-30 pilot report" in out

    quote = project / "quote.txt"
    quote.write_text("I got my Sunday evenings back.")
    base = ["pilot", "testimonial", "acme", "--quote-file", str(quote), "--name", "Sam",
            "--attribution", "first_name_only", "--allow", "website"]  # fmt: skip
    assert run(*base) == 1  # wording not confirmed by the owner
    assert "has not confirmed" in capsys.readouterr().out
    assert run(*base, "--confirmed") == 0
    assert (project / "workspaces/acme/pilot/testimonial.json").exists()
