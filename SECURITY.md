# Security Policy

OpenSecDash is security-related software for homelabs. Please report vulnerabilities responsibly.

## Supported versions

OpenSecDash is currently pre-1.0. Security fixes are applied to the default branch and future tagged releases.

| Version | Supported |
| --- | --- |
| `main` | best effort |
| tagged pre-1.0 releases | best effort |

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Preferred options:

1. Use GitHub's private vulnerability reporting feature if it is enabled for the repository.
2. If private reporting is not available yet, contact the maintainer through the project's published contact channel.

When reporting, please include:

- affected version or commit
- deployment method, if relevant
- clear reproduction steps
- impact description
- whether the issue is already public
- any suggested fix, if you have one

## Scope examples

Security reports may include:

- authentication or authorization bypasses, if authentication is added
- unsafe action execution
- secret leakage in logs or UI
- path traversal or arbitrary file access
- server-side request forgery
- dependency vulnerabilities with a practical impact
- unsafe plugin behavior that affects the core app

Generally out of scope:

- issues requiring full local administrator access
- purely theoretical reports without a plausible impact
- scanner-only reports without explanation
- attacks against intentionally exposed third-party services outside OpenSecDash

## Response expectations

This is an open-source homelab project, so response times may vary. The goal is to:

1. acknowledge valid reports as soon as practical
2. assess impact and affected versions
3. prepare a fix privately when appropriate
4. release and disclose responsibly

Thank you for helping keep OpenSecDash users safe.
