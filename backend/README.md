# Hestia gateway

The Python service on the building gateway: the engine, the trust layer, the
energy manager, the planner, the API, and the dashboard it serves. See the
[repository README](../README.md) and [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

```bash
uv sync                                                   # Python 3.12, locked dependencies
uv run uvicorn --factory hestia.main:create_app --reload  # twin mode, development secrets
uv run pytest                                             # unit, spec and end-to-end tests
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src                                           # strict
uv run bandit -q -r src -c pyproject.toml
uv run hestia --help                                      # users, journal, model checks
```

Settings are environment variables (`HESTIA_*`), all documented in
[.env.example](../.env.example).
