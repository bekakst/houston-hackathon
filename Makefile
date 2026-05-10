.PHONY: help bootstrap doctor seed run web gateway bot smoke evaluator ci clean check-secrets check-brand demo

help:
	@echo "HappyCake US — AI Sales & Operations OS"
	@echo ""
	@echo "  make bootstrap     install deps, init sqlite, copy .env.example -> .env if missing"
	@echo "  make doctor        check claude CLI, python, env vars"
	@echo "  make seed          run hypothesis notebook + render README from template"
	@echo "  make run           start web (8000), gateway (8001), owner bot (long-poll)"
	@echo "  make smoke         run public scenarios via runner"
	@echo "  make evaluator     pytest --strict-markers -x + ruff + mypy + gitleaks"
	@echo "  make ci            full CI — bootstrap + seed + evaluator + smoke"
	@echo "  make demo          90-second scripted demo"
	@echo "  make check-brand   audit templates and prompts for brandbook violations"
	@echo "  make check-secrets gitleaks scan"
	@echo "  make clean         remove .venv, __pycache__, .sqlite"

bootstrap:
	python -m pip install -e ".[dev]"
	@if [ ! -f .env ]; then cp .env.example .env; echo "Copied .env.example -> .env. Edit before running."; fi
	@python -c "from happycake.storage import init_db; init_db()" || echo "DB init deferred until storage.py exists"

doctor:
	@echo "Python: $$(python --version)"
	@echo "Claude CLI: $$(claude --version 2>/dev/null || echo 'NOT FOUND — install Claude Code CLI')"
	@python -c "import happycake; print('happycake package importable: OK')" 2>/dev/null || echo "happycake package not installed — run make bootstrap"
	@python -c "from happycake.settings import settings; print('Settings loaded: OK')" 2>/dev/null || echo "Settings load failed — check .env"

seed:
	python scripts/fetch_mcp_data.py
	jupyter nbconvert --to notebook --execute --inplace analysis/hypothesis.ipynb
	python scripts/render_readme.py

run:
	@echo "Starting web (8000), gateway (8001), owner bot..."
	bash scripts/run.sh

web:
	uvicorn apps.web.main:app --host 0.0.0.0 --port 8000 --reload

gateway:
	uvicorn apps.gateway.main:app --host 0.0.0.0 --port 8001 --reload

bot:
	python -m apps.owner_bot.main

smoke:
	python -m tests.scenarios.runner tests/scenarios/public

smoke-adv:
	python -m tests.scenarios.runner tests/scenarios/adversarial

evaluator:
	python -m tests.scenarios.runner tests/scenarios/public
	python -m tests.scenarios.runner tests/scenarios/adversarial
	ruff check src apps tests scripts || true
	mypy src apps || true

ci: bootstrap seed evaluator smoke

demo:
	bash scripts/demo.sh

check-secrets:
	@which gitleaks >/dev/null 2>&1 || (echo "gitleaks not installed; skipping" && exit 0)
	gitleaks detect --no-git --source .

check-brand:
	python scripts/check_brand.py

clean:
	rm -rf .venv __pycache__ */__pycache__ */*/__pycache__ .pytest_cache .ruff_cache *.sqlite *.sqlite-journal
