# git-submerge

> Dive into your git submodule tree — a TUI for managing deeply nested submodules.

`git submerge` is a terminal user interface for working with git repositories that use deeply nested submodules. It shows a tree view of all submodules, highlights which ones have changes, and lets you update, clean, stash, and commit changes — with automatic cascading commits up the submodule chain.

## Features

- **Tree view** of all submodules (recursive), with color-coded status indicators
- **Diff pane** showing working-directory changes for the selected submodule or individual file
- **Cascading commits** — commit in a nested submodule and the parent superproject is automatically updated and pushed, all the way up the chain
- **File-level actions** — clean, stash, or commit a single file instead of the whole submodule
- **Background operations** — git actions run in threads so the UI stays responsive
- **Filtering** — by default only shows submodules with changes; toggle to see all

## Installation

### From PyPI

```bash
pip install git-submerge
```

### From source (with Poetry)

```bash
git clone https://github.com/ulikoehler/git-submerge.git
cd git-submerge
poetry install
```

### From source (with pip)

```bash
git clone https://github.com/ulikoehler/git-submerge.git
cd git-submerge
pip install .
```

After installation, the `git-submerge` executable will be on your `PATH`. Git automatically discovers it as a custom subcommand, so you can call it with:

```bash
git submerge
```

## Usage

```bash
git submerge [path] [options]
```

### Options

| Flag | Description |
|------|-------------|
| `path` | Base directory of the git superproject (default: current directory) |
| `-u`, `--update` | Run `git submodule update --init --recursive` before launching the TUI |
| `-a`, `--all` | Show all submodules instead of only those with changes |

### Examples

```bash
# Launch in current directory
git submerge

# Launch in a specific repo
git submerge /path/to/my/superproject

# Update all submodules first, then show all (including clean ones)
git submerge --update --all
```

## Keybindings

| Key | Action |
|-----|--------|
| `TAB` | Switch between tree pane and file/diff pane |
| `↑` / `↓` | Navigate tree (left pane) or file list (right pane) |
| `PgUp` / `PgDn` | Scroll the diff view (or `-` / `=`) |
| `U` | Update submodule (`git submodule update --init`) |
| `X` | Clean submodule (`git clean -xdf`) or selected file |
| `S` | Stash changes in submodule or selected file |
| `C` | Commit changes (prompts for message); cascades up the submodule chain |
| `Q` | Quit |

### File-level actions

When focused on the right pane (file list), actions (`X`, `S`, `C`) apply to the selected file instead of the entire submodule.

## How It Works

Git discovers custom subcommands by looking for an executable named `git-<name>` on your `PATH`. Installing `git-submerge` places a `git-submerge` script on your `PATH`, so git treats `git submerge` as a native subcommand.

## Publishing (for maintainers)

```bash
poetry build
poetry publish
```

## Uninstall

```bash
pip uninstall git-submerge
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
