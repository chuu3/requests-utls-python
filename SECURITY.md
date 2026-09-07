# Security policy

## Supported versions

Security fixes target the latest released version and the current development
branch. Older 0.x releases are not maintained as separate security branches.
Report the Python package version, bundled or external engine commit, ABI,
operating system and architecture.

## Reporting

Use this repository's **Security → Report a vulnerability** option when it is
available. If private reporting is unavailable, open an issue asking the
maintainer for a private reporting channel, without vulnerability details.
Do not include credentials, private captures, session tickets or an exploit in
a public issue. A useful private report includes affected versions, a minimal
reproducer, observed impact and relevant configuration.

## Scope

Certificate verification is enabled by default. `verify=False` disables both
certificate-chain and hostname verification; see [proxy and certificate
configuration](docs/proxies.md). Profiles may advertise algorithms or extensions
whose full negotiation behavior is unavailable in the engine; inspect Session
limitations when opting into opaque advertisements.

Request isolation, proxy credential isolation, cancellation, bounded response
decoding and native resource ownership are part of the supported contract.
Report violations even when a malformed peer response is required. Python
wheels bundle a particular engine revision: upgrading an unrelated system Go
installation does not update the engine inside an installed wheel.
