# Security

## Protecting credentials

This is a public repository. Do not commit real credentials, customer data, local databases, or production configuration.

Keep development values in the ignored `.env` file. Keep deployed values in DigitalOcean App Platform's encrypted environment configuration. In particular, these values must remain private:

- `SECRET_KEY`
- `SQUARE_ACCESS_TOKEN`

The Square location ID and Application ID are public identifiers, but the Square access token must only be read by server-side code. Gift-card and remainder-card details are tokenized in Square-hosted fields; raw payment details never reach Flask.

Application logs record operational IDs and states only; they must not include buyer names, email addresses, phone numbers, notes, access tokens, payment tokens, or card details.

Run `python3 scripts/audit_public_repo.py` before publishing. The setup script installs the same audit as a Git pre-commit hook, and GitHub Actions runs it again on pushes and pull requests.

## If a secret is exposed

Revoke or rotate it immediately. Removing it in a later commit is not sufficient because the value remains in Git history. After rotation, remove it from the repository history before making or restoring the repository's public visibility.

Use GitHub's private vulnerability reporting for security issues rather than opening a public issue containing exploit details or sensitive data.
