# Security boundary

The public build is local-first and binds to loopback by default. It has no public-network authentication layer and must not be exposed directly to the Internet.

The repository contains no API key, Cookie, browser profile, captured page, workbook, or production mapping. Provider keys entered at runtime are stored in the user's local application state, not in Git. The included fill adapter is deterministic simulation code and never starts a browser, reads a workbook, or submits data.

Do not use real confidential documents in a public demo recording. Use synthetic fixtures and rotate any key accidentally shown on screen.
