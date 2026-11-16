# Security Policy

## Supported versions
Pre-release: only the latest `main` receives fixes.

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. We aim to acknowledge within 7 days.

## Scope notes
- Clipper processes untrusted media with FFmpeg. Keep FFmpeg updated; media-parsing bugs in FFmpeg are upstream issues,
  but report anything that lets a crafted file escape our limits or sandboxing.
- Before v1.0 there is **no authentication**. The server binds to 127.0.0.1 and checks `Host`/`Origin` headers.
  Exposing it to a network without an authenticating proxy is unsupported.
