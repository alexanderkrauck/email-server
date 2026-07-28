# Multi-User Implementation

Status: implemented.

## Identity

FastMCP's Google OAuth provider authenticates MCP clients. Google Identity Services creates browser sessions for the account dashboard. Both resolve users by the stable Google `sub` claim.

The first authenticated login can explicitly claim the legacy development owner by setting:

```text
EMAILSERVER_CLAIM_LEGACY_ACCOUNTS_ON_FIRST_LOGIN=true
```

This should be enabled only while exactly one intended owner is allowlisted.

## Ownership

`users` own `smtp_configs`. Messages, attachments, sync cursors, and send audits derive ownership through their account. All user-facing lookups join or filter through that owner.

Account IDs and user IDs are never accepted as authority. The authenticated principal is authoritative.

## Mailbox Authorization

Application login and mailbox access are separate:

- Gmail OAuth connections store an encrypted provider refresh token.
- Gmail app passwords, Zoho app passwords, and generic IMAP credentials are encrypted.
- Decryption occurs only at the IMAP/SMTP connection boundary.

## MCP

The connector is hand-authored rather than generated from FastAPI. It contains:

```text
list_mail_accounts
search_mail
search_mail_regex
get_mail
get_thread
get_attachment
send_mail
```

Account administration and synchronization controls are HTTP-only.

## Rollout

1. Start locally and let Alembic migrate and encrypt legacy credentials.
2. Verify all accounts remain assigned to `development-owner`.
3. Configure the Google OAuth application and production secrets.
4. Allowlist the initial owner's Google identity.
5. Enable one-time legacy claim.
6. Log in and verify ownership.
7. Disable legacy claim and add further allowlisted users.
