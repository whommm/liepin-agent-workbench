# AGENTS.md

## Verification commands

This project has no lint/type-check configured. To verify changes, run the
pytest suite:

```bash
uv run python -m pytest
```

Some pre-existing tests in `tests/test_resilience.py` and
`tests/test_excel_greeting.py` are flaky/failing on `master` (unrelated to LLM
rate limiting). New work should at minimum not introduce *new* failures and
pass its own added tests.
