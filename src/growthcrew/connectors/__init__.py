"""Live data sources: metrics arrive through connectors instead of CSV uploads.

Each workspace connects its own accounts. Secrets are encrypted at rest (`keys.encrypt`) and
never returned by the API; scopes are read-only except Brevo, which can send a newsletter only
through `workflow.publish` (an approved draft, a named person, a consented list). Every
connector turns what it reads into the same normalised rows the CSV upload produces, so rows
are matched to pieces the same way and unmatched rows are reported, never guessed.
"""
