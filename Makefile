.PHONY: install dev lint format test tor-check infra-up infra-down clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

lint:
	ruff check tor_pipeline/ tests/
	ruff format --check tor_pipeline/ tests/

format:
	ruff format tor_pipeline/ tests/

test:
	pytest -v

tor-check:
	@curl -s --socks5 127.0.0.1:9050 https://api.ipify.org && echo ""

infra-up:
	docker compose up -d

infra-down:
	docker compose down

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +
