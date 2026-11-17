# compose-example

The project the Docker Compose deployment runs when `ATOMIC_PROJECT_DIR` is
not set: a small Python library managed with [uv](https://docs.astral.sh/uv/),
so atoms have code to read, test, lint and change.

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

The [`python` project image](../../docker/python) installs
the Python in `.python-version` and every package in `uv.lock`, so these run
offline in atomic's sandbox. Point `ATOMIC_PROJECT_DIR` at your own project
instead.
