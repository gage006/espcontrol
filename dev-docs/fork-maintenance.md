# Maintaining this fork

`gage006/espcontrol` retains the independent Now Playing display and tap settings.

## Upstream updates

**Sync Upstream** runs daily at 10:23 UTC and can be run manually. It creates or updates the single `sync/upstream` PR. A clean merge candidate includes both upstream and fork changes; CI is dispatched explicitly because GitHub does not trigger PR workflows for pushes made with its workflow token.

**Merge Validated Upstream** merges only the exact CI-tested candidate, only if it still contains the current fork main and passes the fork update-routing guard. Conflicts stay in the PR for review. Failed sync runs appear in GitHub Actions notifications. Never use a hard reset or GitHub's discard-changes option on main.

## Firmware and editor updates

Firmware manifests and the hosted editor are served from `https://gage006.github.io/espcontrol/`. Release assets come only from this fork. CI, Pages, and release jobs use GitHub-hosted runners.

The fork skips scheduled full-device nightly builds and self-hosted runner cleanup. Firmware Compile and Nightly Firmware Test Build remain available manually. GitHub Pages must use GitHub Actions as its source. The repository's Actions settings must allow workflows to create PRs; GitHub combines that setting with approval permission, although these workflows do not approve reviews.

Publishing remains manual: create a draft release with a unique version tag from tested main, then run **Build Release** for that tag. The workflow compiles and verifies all supported devices before publishing the draft. **Deploy Docs** then publishes the release manifests and binaries to Pages. Until the first release exists, the fork's firmware manifest URL is not available; it must never fall back to upstream firmware.

Existing displays need one ESPHome rebuild and flash from this fork's main to switch their built-in update URL. Keep the user's existing device name, API encryption secret, WiFi configuration, and other local overrides. Set both `espcontrol_component_ref` and the remote package `ref` to `main`; keep the embedded-editor package for matching firmware/UI behavior. Do not install generic factory firmware over a personalized ESPHome configuration without checking which local settings must remain.

Project identity remains `jtenniswood.espcontrol` for compatibility with existing devices and release verification; this does not determine the download source. Original author attribution and donation links are retained.
