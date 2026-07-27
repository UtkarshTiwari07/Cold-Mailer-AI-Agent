.PHONY: up down migrate seed demo test lint fmt worker web

up:
	docker compose up -d postgres redis searxng
	@echo "Waiting for postgres..."
	@until docker compose exec -T postgres pg_isready -U coldmailer >/dev/null 2>&1; do sleep 1; done
	$(MAKE) migrate

down:
	docker compose down

migrate:
	python -m cold_mailer.core.migrate

seed:
	python -m cold_mailer.cli seed --file data/sample_leads.csv

# Full pipeline over 5 sample leads, console transport, no external creds
# required (set CM_LLM__STUB=true to also skip the LLM API).
demo:
	python -m cold_mailer.cli run --limit 5 --transport console

test:
	pytest -q

lint:
	ruff check .
	mypy cold_mailer

fmt:
	ruff format .
	ruff check --fix .

worker:
	arq cold_mailer.pipeline.worker.WorkerSettings

web:
	uvicorn cold_mailer.web.app:app --reload --port 8000
