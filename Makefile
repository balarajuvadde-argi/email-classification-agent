.PHONY: install test lint verify dry-run backfill-live build deploy

install:
	python -m pip install -e '.[dev]'

test:
	python -m pytest

lint:
	python -m ruff check .

verify:
	python scripts/verify_samples.py
	python scripts/safety_audit.py

dry-run:
	email-classifier run

backfill-live:
	email-classifier backfill --live

build:
	sam build

deploy:
	sam deploy --guided
