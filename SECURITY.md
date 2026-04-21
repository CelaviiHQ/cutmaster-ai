# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Celavii-Resolve, please report it responsibly.

**Do not open a public GitHub issue.** Instead, email: **security@celavii.com**

Include:
- A description of the vulnerability
- Steps to reproduce
- The version/commit affected
- Potential impact

We aim to acknowledge reports within 48 hours and provide an initial assessment within 5 business days.

## Supported Versions

Celavii-Resolve is pre-1.0. Only the latest `main` branch receives security updates.

| Version | Supported |
|---------|-----------|
| `main`  | ✅ |
| < latest release | ❌ |

## Scope

**In scope:**
- The Python MCP server (`cutmaster_ai` package)
- The HTTP panel backend (`cutmaster-ai-panel`)
- The installer (`scripts/install.py`)
- Build and packaging scripts

**Out of scope:**
- Vulnerabilities in DaVinci Resolve itself (report to Blackmagic Design)
- Third-party dependencies (report upstream; we will update on disclosure)
- Local-only exploits requiring pre-existing access to the user's machine

## Credentials & API Keys

This project uses several third-party APIs (Gemini, Deepgram, Anthropic). Credentials are loaded from environment variables or `.env` files — **never commit keys**.

If a key is accidentally committed:
1. Rotate the credential immediately at the provider
2. Notify the team via `security@celavii.com`
3. Do not rely on `git filter-repo` alone — assume the key is compromised the moment it hits a public commit

## Development Practices

- Pre-commit hooks scan for common secret formats and hardcoded local paths
- GitHub secret scanning + push protection are enabled on the public repo
- Dependabot monitors dependencies for known vulnerabilities
