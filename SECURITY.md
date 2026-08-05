# Security

## Protecting credentials

This is a public repository. Do not commit real credentials, customer data, local databases, or production configuration.

Keep development values in the ignored `.env` file. Keep deployed values in AWS Secrets Manager, AWS Systems Manager Parameter Store, or the encrypted environment configuration used by the eventual deployment. In particular, these values must remain private:

- `SECRET_KEY`
- `SQUARE_ACCESS_TOKEN`
- Square webhook signature keys
- AWS access keys and session credentials
- Email-provider credentials

The Square application ID and location ID may eventually be delivered to the browser, but the Square access token must only be read by server-side code.

Run `python3 scripts/audit_public_repo.py` before publishing. The setup script installs the same audit as a Git pre-commit hook, and GitHub Actions runs it again on pushes and pull requests.

## If a secret is exposed

Revoke or rotate it immediately. Removing it in a later commit is not sufficient because the value remains in Git history. After rotation, remove it from the repository history before making or restoring the repository's public visibility.

Use GitHub's private vulnerability reporting for security issues rather than opening a public issue containing exploit details or sensitive data.
