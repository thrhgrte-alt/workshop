# Publishing the five repositories

**Status: none of them has been published.** They exist as local Git repositories (and as bundles in `bundles/`). No remote repository was created by this work: the session that built them could
only reach the one repository these files are pushed to. Create the remotes yourself with one of the routes below. Keep them **private** until you have read each `ASSET_LICENSING.md`.

## Route A: GitHub CLI (one command)
```bash
sh suite/restore_repos.sh                     # if repos/ does not exist yet (recreates them from bundles/)
gh auth login                                 # once
sh suite/publish.sh <your-github-username-or-org>
```
`publish.sh` runs `gh repo create <owner>/<name> --private --source . --remote origin --push` in each repository and stops on the first error.

## Route B: by hand, per repository
```bash
cd repos/<name>
# 1. create an EMPTY private repository named <name> on github.com (no README/licence/.gitignore)
git remote add origin https://github.com/<owner>/<name>.git
git push -u origin main
```
Names: `substance-designer-ai`, `roblox-vfx-ai`, `roblox-level-design-ai`, `modular-set-dressing-ai`, `concept-art-ai`, `asset-roblox-preflight`, `luau-reviewer`, `roblox-economy-balancer`.

## Before you push anything
- Everything in the repositories is synthetic or generated (see each `examples/`). Add your own references **outside Git** (`<PREFIX>_ASSET_ROOT`); `workspace/` is git-ignored.
- Check `LICENSE` (MIT for code and docs) and `ASSET_LICENSING.md` (assets, generated output, training data) are what you want.
- Large binaries belong in your own asset store or Git LFS, not in these repositories.
- Run `python -m pytest` in each repository on your machine. Python 3.10+; Pillow, numpy and (for Luau mocks) `lupa` are installed by `pip install -e ".[dev]"`.

## Connecting an agent
Each README has a table of per-client instruction files, skill locations and MCP configuration (`adapters/mcp-clients/`). They document where each vendor looks for files **as understood when written**;
none was tested against a live client. Roblox Studio's own MCP server is a separate server: add it to the same client next to the repository's server.
