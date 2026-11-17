# stc-tests tool

The consultancy-side tool of the test solution: reads the specifications `stc spec` writes (through
the contract in `contracts/`), validates them and tracks what changed between runs.

```bash
uv sync
uv run stc-tests check --all --specs ../../specs-out        # validate against the contract
uv run stc-tests ingest APP --specs DIR --output DIR [--dry-run]   # new / changed / retired
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

`--specs` defaults to `STC_SPECS` (else `./specs`), `--output` to `STC_TESTS` (else `./testsuites`).
It never imports `stc`; `tests/test_independence.py` enforces it.
