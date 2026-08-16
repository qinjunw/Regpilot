# Security boundary

The public build is local-first and binds to loopback by default. It has no public-network authentication layer and must not be exposed directly to the Internet.

The repository contains no API key, Cookie, browser profile, captured page, workbook, or production mapping. Provider keys entered at runtime are stored as plaintext JSON in the user's local application state, not in Git or an operating-system credential vault. The included fill adapter is deterministic simulation code and never starts a browser, reads a production fill workbook, or submits data. Separately, the evidence ingestion module can read an `.xlsx` file that the user explicitly supplies as a regulation source.

By default, sessions, extracted source evidence, source paths, regulation indexes, exports, and provider settings are stored under `%LOCALAPPDATA%\RegPilot\`. A configured model request sends prompts, tool context, and selected evidence to the configured `Base URL`; only use data approved for that provider.

Do not use real confidential documents in a public demo recording. Use synthetic fixtures and rotate any key accidentally shown on screen.
