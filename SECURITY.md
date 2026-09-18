# Security policy

ModernMount edits persistent mount configuration and can enroll a TPM2 token in
a LUKS2 header. Treat security reports as potentially sensitive.

## Supported versions

No production release exists yet. Security fixes are made on the current main
development branch.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose secrets,
damage data, bypass authorization, modify protected volumes, or enable command
execution. Use GitHub's private vulnerability reporting feature when it is
available in the repository's Security tab. Until that feature is enabled, retain the
report privately rather than disclosing it in a public issue.

Include the affected revision, impact, reproduction steps using disposable
test data, and any proposed mitigation. Never include real passwords, recovery
keys, TPM secrets, machine identifiers, or unredacted disk metadata.

## Scope priorities

Reports are especially valuable for:

- bypasses of the data-volume and protected-path checks;
- unsafe `/etc/fstab` or `/etc/crypttab` generation;
- LUKS keyslot loss or unintended TPM enrollment;
- password exposure through arguments, environment, logs, or persistent files;
- D-Bus or Polkit authorization bypasses;
- arbitrary command or file-write behavior in the host helper;
- races involving device replacement, stale configuration, or rollback.

The project will acknowledge reports after a private reporting address is
configured. Response-time commitments will be added before the first release.
