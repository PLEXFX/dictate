# Security policy

## Supported releases

Only the newest release published on the official
[`PLEXFX/dictate`](https://github.com/PLEXFX/dictate) Releases page is
supported.

## Reporting a vulnerability

Please do not publish a security issue, exploit, personal information, or a
working proof of concept in a public issue. Contact the maintainer through the
repository's GitHub profile with a short description, affected version, and
safe reproduction steps. You should receive an acknowledgement before any
public discussion.

## Update trust model

Dictate accepts an in-app update only when all of the following are true:

1. It is the exact installer asset for a release from `PLEXFX/dictate`.
2. Its release checksum asset matches the downloaded bytes.
3. Windows validates the installer signature and it matches Dictate's pinned
   code-signing certificate.

An unsigned or incorrectly signed release is rejected rather than installed.
