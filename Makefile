
test:
	uv run pytest -q --cov=growthcrew --cov-report=term-missing:skip-covered

lint:
	uv run ruff check . && uv run ruff format --check .

# Offline evals: no API key, no cost. Writes evals/results/scorecard.{md,json}.
eval:
	uv run python -m evals.run

# Adds the evals that call the model. Costs money. Use LIMIT=5 for a cheap partial run.
eval-live:
	uv run python -m evals.run --live $(if $(LIMIT),--limit $(LIMIT),)

# Everything in Docker, with the sample brand loaded: http://localhost:3000
up:
	docker compose up --build

# The same without Docker: seeds the sample brand into the local database.
demo:
	uv run python -m evals.demo_seed
