# Checks and Releases

This page describes how verification and release safety work. Use the generated
[Check Matrix](check-matrix.md) or a [Task Playbook](playbooks/README.md) for the
exact commands required by a change. Tool installation belongs in
[Development Environment](development-environment.md).

## Automatic Upstream Sync (Fork)

The `Upstream Sync` workflow checks `jtenniswood/espcontrol:main` every 15
minutes for `gage006/espcontrol`. It merges upstream into the dedicated
`sync/upstream` branch, preserving fork changes and upstream history,
then maintains one PR. No force-push or squash merge is used. Conflicts stop
the run and leave `main` unchanged. A separate recovery PR exposes conflicting
upstream changes without resetting the integration branch. Existing open recovery
PRs are reused and never automatically merged.

Branch CI first regenerates icon outputs where needed; generated commits trigger
fresh PR CI using the dedicated token. The built-in Actions token dispatches
branch CI, so the dedicated token still only needs Actions read access.
For each validated sync commit it posts `@codex review`. Automatic merging waits
for Codex's thumbs-up on that commit's request (or an explicit Codex approval
on that commit), successful PR CI, the trusted main branch's fork configuration
guard, and GitHub's mergeability checks. Findings,
quota/error messages, missing reviews, failed checks, and unresolved review
conversations leave the PR open. This is deliberately conservative: a Codex
text response without the recognized success signal requires manual review.
Review findings are not automatically fixed. A new upstream or main commit
updates the PR and requires a fresh review and CI run.

### One-Time Setup

1. Enable Codex Code review for this fork in
   [Codex settings](https://chatgpt.com/codex/settings/code-review).
2. Add the Actions repository secret `UPSTREAM_SYNC_TOKEN`: a dedicated,
   fine-grained GitHub personal access token limited to this fork, with
   Contents, Pull requests, Issues, and Workflows read/write; Actions and
   Administration read-only. The workflow uses Administration read access to
   verify branch protection. Set an expiration and rotate the secret before
   it expires. A dedicated token allows CI and Codex to receive the generated
   PR/comment events; the default Actions token is not an unattended substitute.
   Never put a token in repository files or PR comments.
3. Protect `main`: require a pull request, require the `CI Gate` status check,
   require branches to be up to date, require conversation resolution, and
   enforce these rules for administrators (no bypass). Do not require a human
   approval if unattended sync is desired; Codex's thumbs-up is not a GitHub
   approval. These branch rules also apply to ordinary PRs.
4. Merge the automation setup PR, then run **Actions > Upstream Sync > Run
   workflow** with `dry_run` checked. Verify the upstream comparison in the
   run summary. Run again with `dry_run` unchecked to create the first sync PR.
   Confirm CI starts and Codex responds; the next scheduled run merges only
   when every condition is satisfied. GitHub auto-merge need not be enabled.

The owner explicitly authorized automatic upstream merges after AI review
and passing checks. This exception applies only to upstream sync PRs; normal
feature/fix PRs still wait for user testing confirmation. No workflow flashes
devices or claims physical-device testing. CI includes the existing check
graph and documentation build; this does not add full device firmware compiles.

Schedules are approximate and may be delayed by GitHub. Public-repository
schedules can be disabled after 60 days without repository activity; re-enable
the workflow from Actions if that occurs. Disable `Upstream Sync` in Actions
to pause it. Check failed-run notifications for conflicts or expired tokens.
The workflow does not update local clones, publish releases, or close issues.

References: [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
and [Codex GitHub review](https://developers.openai.com/codex/integrations/github).

## Dependency-Aware Check Graph

The public npm check commands enter the dependency-aware task graph. A focused
task automatically includes its declared prerequisites; the product, fast, CI,
all, and release profiles run broader assurance sets. The release profile covers
firmware host-service tests, cross-language saved-configuration parity, browser
journeys, device-matrix validation, generated outputs, documentation, and
release manifests.

`scripts/check_tasks_data.py` is the maintained source for task commands,
dependencies, profiles, domains, input paths, cache inputs, tool requirements,
and parallel safety. `scripts/check_tasks.py` plans and executes that registry.
It can list registered tasks or explain a profile before running it.

## Parallel Execution

Normal npm aliases and CI use one worker. The explicit `check:parallel` entry
allows no more than four workers, and only dependency-independent tasks marked
parallel-safe may overlap. Browser, release, Git-state, and shared-output checks
always run alone; release profiles remain single-worker.

After the first failure, no new tasks start. Already-running tasks finish and
dependent tasks are reported as blocked. The decision to keep parallel mode
opt-in is supported by the dated
[parallel-check benchmark](history/parallel-check-benchmark.md).

## Deterministic Result Cache

Successful deterministic local checks are cached by content in the repository's
shared Git directory, so linked worktrees can reuse results. A key includes the
task and command, dependency keys, declared authored and generated inputs,
runner and registry code, lockfiles, platform, tool versions, and declared
environment variables. Any change to those values causes a fresh check.

Only successful results are stored, corrupt entries are misses, and checks that
depend on Git history, release state, external state, or shared output are never
cached. Browser smoke is cacheable only when Playwright, Node, Chromium, layouts,
and web inputs are all fingerprinted. `CI=true` disables the result cache so CI
always runs from scratch.

The task runner supports cache status, cache clearing, and a `--no-cache` option
for deliberate fresh execution.

## Changed-Path Planning

The changed-path planner considers committed, staged, unstaged, renamed,
deleted, and untracked paths relative to `main`. Unknown paths and changes to
shared helpers, generators, validators, the task runner, registry, lockfile, or
workflow definitions select the complete fast profile. Domain filters can
narrow a deliberately selected CI profile, but changed-path planning never
reduces CI coverage.

## Confidence Levels

Playbooks use three consistent levels:

- Minimum proves the narrow contract touched by a change.
- Recommended runs the normal product-level safety net for that workflow.
- Release-grade adds broad checks, browser journeys, documentation builds, or
  firmware compiles when release-facing behavior is involved.

A successful compile is automated evidence, not physical-device confirmation.
Record flashing and device behavior separately in the pull request.

## Release Boundary

Firmware releases start as private GitHub drafts. The manually dispatched build
workflow checks out one immutable tag in every job, materializes the selected
tag's web compatibility entry in the private checkout, builds every supported
target, and writes only publishable files to the distribution. Generated source
and build caches are not assets.

The workflow verifies every manifest, embedded version, expected filename,
checksum, byte size, and the final remote asset inventory while the release is
still private. Publication occurs only after the complete remote inventory
matches the verified local distribution. Any build, upload, or verification
failure leaves the release as a draft.

Current generated-output ownership is listed in
[Source of Truth Contract](source-of-truth.md); upgrade-sensitive public names
and formats are listed in [Compatibility Contract](compatibility-contract.md).
Use [Change the Release Workflow](playbooks/change-release-workflow.md) for the
exact edit and verification procedure.
