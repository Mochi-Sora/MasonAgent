# Contributing to nanobot

Thank you for being here.

nanobot is built with a simple belief: good tools should feel calm, clear, and humane.
We care deeply about useful features, but we also believe in achieving more with less:
solutions should be powerful without becoming heavy, and ambitious without becoming
needlessly complicated.

This guide is not only about how to open a PR. It is also about how we hope to build
software together: with care, clarity, and respect for the next person reading the code.

## Maintainers

Maintainers are community stewards who help review, organize, and maintain the project. The list below describes each maintainer's current open-source project responsibilities.

| Maintainer | Role |
|------------|------|
| [@re-bin](https://github.com/re-bin) | Project lead; reviews community PRs and handles merges |
| [@chengyongru](https://github.com/chengyongru) | Reviews community PRs and may approve them; merges are handled by the project lead |

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

Mason lives at `Mochi-Sora/MasonAgent` and tracks upstream `HKUDS/nanobot` as a second
remote. Set up both once:

```bash
git clone https://github.com/Mochi-Sora/MasonAgent.git
cd MasonAgent
git remote add upstream https://github.com/HKUDS/nanobot.git
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
ruff check nanobot/

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

We care about more than passing lint. We want nanobot to stay small, calm, and readable.

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

## Fork Maintenance

The public repository is <https://github.com/Mochi-Sora/MasonAgent>. Several files
still identify the upstream `HKUDS/nanobot` project; update them when the package and
documentation locations are decided, so the fork stops presenting upstream's identity:

| Location | What it controls |
|---|---|
| `nanobot/agent/tools/mcp_oauth.py` (`_CLIENT_URI`, `_LOGO_URI`, `software_id`) | Identity sent to remote MCP OAuth servers during dynamic client registration |
| `nanobot/providers/openai_compat_provider.py`, `nanobot/providers/image_generation.py` (`HTTP-Referer`) | Attribution header sent to OpenRouter-compatible gateways |
| `scripts/update_readme_contributors.py` (`REPOSITORY`, `MAINTAINERS`) | Contributor-wall sync; the rewritten README has no contributor markers yet, so the script currently has no target |
| `pyproject.toml` (`[project.urls]`, when added) | Package metadata on PyPI |
| Community links in `.github/ISSUE_TEMPLATE/config.yml` and `COMMUNICATION.md` | Upstream Discussions and chat groups |

The installers already point at `Mochi-Sora/MasonAgent` (`default_git_url` in
`scripts/install.sh`, `$DefaultGitUrl` in `scripts/install.ps1`), and clone commands in
these docs use the fork. Some upstream identity strings are asserted by tests
(`tests/tools/test_mcp_oauth.py`, `tests/providers/test_litellm_kwargs.py`); update them
in the same change.

The installers themselves need no changes: they install the checkout they run from, or clone
whatever URL is passed with `--git` / `NANOBOT_INSTALL_GIT`. `README.md` keeps its credit
link to upstream nanobot; that is intentional.

## Questions?

If you have questions, ideas, or half-formed insights, you are warmly welcome here.

Please feel free to open an [issue](https://github.com/Mochi-Sora/MasonAgent/issues) on this repository, or reach out to the upstream nanobot community:

- [Discord](https://discord.gg/MnCvHqpUGB)
- [Feishu/WeChat](./COMMUNICATION.md)
- Email: Xubin Ren (@Re-bin) — <xubinrencs@gmail.com>

Thank you for spending your time and care on nanobot. We would love for more people to participate in this community, and we genuinely welcome contributions of all sizes.
