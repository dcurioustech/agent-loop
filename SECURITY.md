# Security Policy

## Supported versions

agent-loop is currently in early development. Security fixes are made on the
latest released version and the default branch only. Older releases are not
supported; users should upgrade to the newest release before reporting an
issue.

## Reporting a vulnerability

Please do not report suspected vulnerabilities in a public issue, discussion,
pull request, or committed audit log. Use the repository owner's private
contact method or GitHub's **Report a vulnerability** feature, when available.
Include:

- the affected version or commit;
- a description of the vulnerability and its impact;
- minimal reproduction steps or a proof of concept;
- any known mitigations; and
- a safe way to contact you for follow-up.

Remove real credentials and personal data from all reports. Use placeholder
tokens in reproductions.

You should receive an acknowledgement within seven days. The maintainers will
investigate, keep you informed of material progress, and coordinate disclosure
after a fix is available. Please allow a reasonable remediation period before
public disclosure.

## Security considerations for users

agent-loop launches third-party coding-agent CLIs and can enable their
dangerous or unattended modes. Run it only in repositories and environments
you trust, review provider permissions, and use least-privilege credentials.

Run logs may be committed to Git history. The `redacted` audit level is
best-effort and cannot recognize every secret format; the default `off` level
is the safest choice for preventing raw provider output from entering logs.
Inspect all commits and logs before pushing them to a public remote.
