# Repository instructions

- Treat the official RTZR authentication, file STT, and rate-limit documentation as the API contract.
- Never print, log, search for, or commit credential values, access tokens, private labels, or raw private evaluation results.
- Keep generic environment variable names STT_CLIENT_ID and STT_CLIENT_SECRET in public code.
- Add or update tests before changing request fields, retry behavior, normalization, CER aggregation, or output formats.
- Do not silently change references, sample membership, normalization rules, or the fixed API preset to improve a metric.
- Unit and contract tests must not call the live API. A live smoke test is an explicit manual action.
- Before declaring work complete, run make check and inspect tracked files and Git history for sensitive content.
