# Contributing to ModernMount

Thanks for helping make Linux storage setup safer and easier.

## Before contributing

Please open an issue before beginning a large change. For security-sensitive
findings, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

By submitting a contribution, you agree that it may be distributed under the
project's GNU General Public License, version 3 or any later version
([GPL-3.0-or-later](LICENSE)). You retain copyright in work you create. No
separate contributor license agreement is required. Material already carrying
a separate license, as identified in [COPYRIGHT](COPYRIGHT), retains its terms.

## Development

Use the system Python with PyGObject, GTK4, and libadwaita installed. Run the
safe demo backend while working on UI changes:

```sh
make demo
make test
```

Tests must not edit `/etc/fstab`, `/etc/crypttab`, LUKS headers, TPM state, or
real block devices. Real integration testing belongs on disposable volumes and
must be described explicitly in the pull request.

## Pull requests

- Keep each pull request focused and explain the user-visible behavior.
- Add meaningful tests for configuration planning, D-Bus encoding, rollback,
  and privileged helper behavior when those areas change.
- Run `make test` and report any additional manual testing.
- Do not weaken system-volume protections, stale-state checks, authorization,
  or rollback behavior without a documented security rationale.
- Do not add secrets, personal disk identifiers, real UUIDs, or machine logs
  containing sensitive data.
- Update user-facing documentation when behavior or support boundaries change.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
