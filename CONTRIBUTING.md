# Contributing to mason

Thank you for being here.

mason is built with a simple belief: good tools should feel calm, clear, and humane.
We care deeply about useful features, but we also believe in achieving more with less:
solutions should be powerful without becoming heavy, and ambitious without becoming
needlessly complicated.

This guide is not only about how to open a PR. It is also about how we hope to build
software together: with care, clarity, and respect for the next person reading the code.

## Maintainers

Mason is maintained by a single person. Reviews and merges are handled by them;
please be patient if a response takes a little while.

| Maintainer | Role |
|------------|------|
| [@Mochi-Sora](https://github.com/Mochi-Sora) | Maintainer; reviews and merges, releases, security reports |

Contact: **mochi0216@proton.me**. For anything security related, prefer the private
channel described in [`SECURITY.md`](./SECURITY.md).

## Contribution Flow

### What Should I Open a PR For?

PRs are welcome for:

- New features or functionality
- Bug fixes with no behavior changes
- Documentation improvements
- Minor tweaks that don't affect functionality
- Refactoring that is clearly scoped and easy to review
- Changes to APIs or configuration, when the impact is documented

For riskier or larger changes, please open an issue or draft PR early so the
shape of the work can be discussed before the implementation grows too large.

### Starting Work

Mason lives at `Mochi-Sora/MasonAgent`. Clone it:

```bash
git clone https://github.com/Mochi-Sora/MasonAgent.git
cd MasonAgent
```

Before making changes, sync your checkout and create a topic branch:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c your-topic-branch
```

Use `upstream` (not `origin`) when you want to test against the original project's
tip; Mason's own development happens on `origin`.

Keep unrelated local changes out of the topic branch. If your checkout already has
work in progress, use a separate worktree or finish that work before starting a
new branch.

## Development Setup

Keep setup boring and reliable. The goal is to get you into the code quickly:

```bash
# Clone the repository
git clone https://github.com/Mochi-Sora/MasonAgent.git
cd MasonAgent

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint code
ruff check mason/

# Format code — optional. The existing tree predates `ruff format`,
# so running it broadly produces large unrelated diffs.
# Do not mix mechanical formatting churn into a functional PR.
# Use formatting only for the exact code your change intentionally touches.
ruff format <files-you-changed>
```

### Strict Type Checking

Strict type checking covers optional providers and channels. Reproduce the CI environment
with the same dependency sources and commands:

```bash
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync basedpyright
```

Keep `--no-sync` on the final commands: channel dependencies come from their package
manifests and are installed explicitly by the setup step.

## Contribution License

By submitting a contribution, you confirm that you have the right to submit it
and agree that it will be licensed under the project's
[AGPL-3.0-or-later](LICENSE) license.

## Code Style

We care about more than passing lint. We want mason to stay small, calm, and readable.

When contributing, please aim for code that feels:

- Simple: prefer the smallest change that solves the real problem
- Clear: optimize for the next reader, not for cleverness
- Decoupled: keep boundaries clean and avoid unnecessary new abstractions
- Honest: do not hide complexity, but do not create extra complexity either
- Durable: choose solutions that are easy to maintain, test, and extend

In practice:

- Line length: 100 characters (`ruff`)
- Target: Python 3.11+
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored)
- Async: uses `asyncio` throughout; pytest with `asyncio_mode = "auto"`
- Prefer readable code over magical code
- Prefer focused patches over broad rewrites
- Do not mix mechanical formatting, line wrapping, import sorting, or quote churn
  into a feature or bugfix PR. If formatting cleanup is needed, make it a
  separate formatting-only PR.
- If a new abstraction is introduced, it should clearly reduce complexity rather than move it around

## Modifying CI Workflows

If your PR touches `.github/workflows/`, please keep the CI within
GitHub Actions' free tier:

- Use only standard GitHub-hosted runners (`ubuntu-latest`, `windows-latest`)
- Avoid macOS runners, larger runners (`*-cores`, `*-xlarge`, `*-gpu`),
  and self-hosted runners
- Avoid uploading large artifacts or using long retention
- Avoid paid Marketplace actions

If your change genuinely needs to step outside this, please call it out
explicitly in the PR description so it can be discussed before merge.

## Release Packaging Contract

Use the [release checklist](./docs/releasing.md) for candidate preparation, package checks,
and the final publication handoff.

The project ships as a single platform-neutral Python package. Publish in this order:

1. Before pushing a tag, set the package version, verify the exact candidate, and build the
   source distribution and wheel.
2. Merge the release preparation, verify that its packaged sources match the checked candidate,
   then publish the matching GitHub release tag (`vX.Y.Z`). Recheck any changed sources first.
3. Upload the same `X.Y.Z` source distribution and wheel to PyPI.

The source distribution remains platform-neutral. Do not upload artifacts that were not built
from the tagged commit.

## Upstream Relationship

Mason began as a derivative of the MIT-licensed `HKUDS/nanobot` and is now an independent
project: upstream is no longer tracked and is not a merge target. The credit in
[`README.md`](./README.md) and the MIT notice in [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md)
are intentional and must be preserved.

Project-owned identity strings point at this repository:

| Location | What it controls |
|---|---|
| `mason/agent/tools/mcp_oauth.py` (`_CLIENT_URI`, `_LOGO_URI`, `software_id`) | Identity sent to remote MCP OAuth servers during dynamic client registration |
| `mason/providers/openai_compat_provider.py`, `mason/providers/image_generation.py` (`HTTP-Referer`) | Attribution header sent to OpenRouter-compatible gateways |
| Community links in `.github/ISSUE_TEMPLATE/config.yml` and `COMMUNICATION.md` | Issues and contact routes |
| Installer defaults (`default_git_url` in `scripts/install.sh`, `$DefaultGitUrl` in `scripts/install.ps1`) | Clone URL for one-line installs |
| `pyproject.toml` (`[project.urls]`, when added) | Package metadata on PyPI — point it at this repository |

When one of these locations changes, update the tests that assert it
(`tests/tools/test_mcp_oauth.py`, `tests/providers/test_litellm_kwargs.py`). The installers
install the checkout they run from, or clone the URL passed with `--git` / `MASON_INSTALL_GIT`.

The `nanobot` references in the compatibility layers describe this project's own pre-rename
history and must stay: the `~/.nanobot` migration in `mason/config/compat.py`, `NANOBOT_*`
environment variables, and legacy `dev.nanobot` plugin metadata keys.

## Questions?

If you have questions, ideas, or half-formed insights, you are warmly welcome here.

Please open an [issue](https://github.com/Mochi-Sora/MasonAgent/issues) on this
repository. For private questions, email **mochi0216@proton.me**.

Upstream nanobot has its own community, listed in the credit section of the
README; Mason is not affiliated with it.

Thank you for spending your time and care on mason. We would love for more people to participate in this community, and we genuinely welcome contributions of all sizes.
