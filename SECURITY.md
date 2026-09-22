# Security policy

## Reporting a vulnerability

Do not open a public issue for a vulnerability or expose credentials in an
issue, pull request, or commit. Report it privately to the repository owner,
including the affected endpoint, impact, and steps to reproduce.

## Deployment requirements

- Keep `.env` only in the deployment secret store; commit `.env.example` only.
- Use a unique, high-entropy `JWT_SECRET_KEY` (at least 32 characters) in
  production and rotate it immediately if it is exposed.
- Use a least-privilege MongoDB user, TLS, and an Atlas/IP network allow-list.
- Set `ENVIRONMENT=production`, explicit HTTPS `CORS_ORIGINS`, and explicit
  `TRUSTED_HOSTS` for the deployed domains.
- Terminate TLS at the load balancer or reverse proxy; never expose MongoDB to
  the public internet.
- Enable GitHub secret scanning, push protection, Dependabot alerts, and branch
  protection on the GitHub repository.

## Scope

This repository has automated safeguards, but no application can truthfully be
described as "fully secure." Keep dependencies patched and review deployment
infrastructure and access controls regularly.
