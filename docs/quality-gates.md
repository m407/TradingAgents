# Development quality gates

Adopted from [Dev Quality Gates](https://github.com/mokevnin/dev-quality-gates)
on 2026-11-05. Tool releases and maintenance status were checked at adoption.

## Running the checks

`mise run check` is the complete local and CI entry point. Versions live in
`mise.toml`; Python dependencies remain in `pyproject.toml` and generated
`uv.lock`. The test task uses `.venv-quality`, leaving the application's virtual
environment independent. Schema checks use schemas bundled with the pinned
check-jsonschema release, avoiding mutable remote schemas.

`mise run hooks:install` installs hk in this clone. Commit hooks check staged
files, including staged secrets; pre-push runs `mise run check:local`. The
complete CI command also checks vulnerability data. Weekly CI runs catch new
advisories and expired exceptions. All check failures produce nonzero exits.

## Scope

| Concern | Enforcement |
| --- | --- |
| Python lint | Existing Ruff rules, including tracked skill examples/scripts |
| Tests | Existing offline pytest suite; Python 3.11–3.14 CI matrix plus a non-UTC run |
| Packaging | Existing fresh install; source archive/wheel build and clean wheel imports |
| Workflow correctness/security | jactionlint, with ShellCheck for embedded shell |
| Secrets | Betterleaks over Git history and current tracked/non-ignored regular files |
| Dependency vulnerabilities | OSV-Scanner over uv.lock and a fresh wheel installation |
| Config validity | check-jsonschema for Compose and GitHub issue forms/config |
| Dockerfile | Hadolint, including informational findings |
| Dependency updates | Dependabot for uv and GitHub Actions |
| Exception expiry | `scripts/quality.py exceptions`, including regression tests |

The previous Renovate `config.js` is replaced by `.github/dependabot.yml`.
Dependabot opens weekly updates with a seven-day cooldown. Minor/patch updates
are grouped; major updates remain individual PRs. All Dependabot PRs are eligible
for squash-merge after the current commit passes the complete CI run.

The privileged merge workflow executes no PR code and consumes no build
artifacts. It verifies the workflow path, repository, Dependabot author and
branch, open/non-draft state, default target branch, current head SHA, and each
required CI job. The merge API receives that SHA to reject concurrent changes.
Keep its required-job names in sync when changing the CI matrix. GitHub branch
protection still applies; the workflow does not bypass it.

These workflows become active after landing on the default branch. GitHub
Actions and Dependabot version updates must be enabled for the repository;
forks may require enabling Actions explicitly. Tool versions in `mise.toml`
need manual updates because Dependabot has no mise ecosystem.

## Existing findings and exceptions

The initial Ruff scan reported 58 findings: UP045 (29), UP017 (21), UP035 (3),
and one each of SIM113, UP006, SIM105, I001, B905. Reviewed safe fixes and three
small manual fixes resolved them without disabling rules. Chained autofixes
also repaired resulting import findings. Existing whole-project formatting
deferral remains in place.

The original tracked workflows had 15 jactionlint findings: missing timeouts
(4), missing permissions (3), and unpinned actions (8). These were corrected.
Hadolint's one DL3066 finding was fixed by explicitly creating/running UID 1000.

OSV identified four affected locked packages. Targeted upgrades were:

- aiohttp 3.14.3 → 3.14.4
- langgraph-checkpoint-sqlite 3.1.1 → 3.1.2 (also the declared minimum)
- pyasn1 0.6.4 → 0.6.5
- urllib3 2.7.0 → 2.8.0

There is no vulnerability baseline and no Ruff baseline. Two exceptions were
explicitly approved, both requiring review on **2027-02-05**:

1. One exact Betterleaks fingerprint for an artificial password in a historical
   redaction test, recorded in `.betterleaksignore`. It does not allowlist the
   file or rule generally.
2. One `dangerous-triggers` finding on the merge workflow's `workflow_run`
   declaration. The reason is documented immediately above the suppression.

The exception checker fails on missing/invalid dates and on or after the review
date. New exceptions need a human decision and a narrow scope.

## Verification

The complete local `GITHUB_ACTIONS=true mise run check` passed: 3,184 pytest tests and 100 subtests
(one integration test deselected), package build/clean wheel imports, and both
vulnerability scans. A first attempt hit a dependency-download network error;
the retry completed successfully.

GitHub validation exposed two portability issues: the timezone matrix entry
overwrote the normal Python 3.12 run, and ANSI styling broke a CLI help assertion.
The timezone entry is now an additional run; the help test checks visible text
in both colored and plain modes.

Scratch-branch probes confirmed nonzero exits for Ruff through the aggregate
command, jactionlint, Hadolint, ShellCheck, invalid Compose schemas, OSV on a
known-vulnerable dependency, expired exceptions, and a staged fake credential
through the actual hk commit hook. Fixtures were removed after each probe.

Twelve mocked GitHub API scenarios exercised the merge workflow's script:
the current green Dependabot PR merged with a SHA precondition; stale heads,
human authors, forks, draft/closed PRs, other targets/workflows, and
failed/skipped/missing jobs did not merge. These checks do not substitute for
observing the workflow on GitHub after it lands.

## Deferred and unavailable categories

Type checking (ty), dependency tidiness (deptry), import contracts
(import-linter), and coverage thresholds need separately measured baselines or
agreed policies. Agent-config/documentation linting is deferred because it
would expand this adoption into bundled skills and generated OpenWiki content.
Generated OpenWiki pages remain owned by their existing generator.

No Docker images are built. JavaScript application checks, other language
stacks, Terraform, Kubernetes, database migrations, and API-contract tools do
not match the current project. The recommendation list has no settled pick
for container-image scans, IaC security, YAML style, data-flow analysis, or
licence/supply-chain policy; those categories remain empty.
