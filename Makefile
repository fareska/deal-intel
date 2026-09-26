.PHONY: up down migrate ingest generate-slack test lint check lock

up:
	docker compose up -d --build --wait

down:
	docker compose down

migrate:
	docker compose run --rm app alembic upgrade head

ingest:
	docker compose run --rm app deal-intel ingest --path synthetic_data

generate-slack:
	uv run deal-intel generate-slack

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

check: lint test

lock:
	uv lock
