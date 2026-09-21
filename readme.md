# tomlinks

Transactional dotfile and configuration manager.

Single-file Python script that copies configs between backup packages and system locations. No symlinks, no daemon, no database—just INI manifests and atomic file operations.

## Installation

### install.sh (recommended)

```bash
./install.sh
```

Copies `tomlinks.py` to `/usr/local/bin/tomlinks` (requires sudo).

### uv

```bash
uv tool install .
```

### pip

```bash
pip install .
```

Each line of `tomlinks.ini` maps a file in the package to its place on the system:

```
<file-in-package> = <destination-on-system>
```

- **left side** — relative to the package directory (the file that lives in the package)
- **right side** — absolute path on the system; `~` is expanded to the current user's home

So `examples/fish/tomlinks.ini` says: *my `config.fish` belongs in `~/.config/fish/config.fish`*:

```ini
config.fish = ~/.config/fish/config.fish
```

Both sides can be single files or whole directories (copied recursively).

## Tutorial

### 1. Lay out your dotfiles repo as packages

One directory per program, each with its own `tomlinks.ini` and files:

```
~/dotfiles/
├── fish/
│   ├── tomlinks.ini
│   └── config.fish
└── git/
    ├── tomlinks.ini
    └── gitconfig
```

### 2. Describe destinations in each manifest

`fish/tomlinks.ini`:

```ini
config.fish = ~/.config/fish/config.fish
```

`git/tomlinks.ini`:

```ini
gitconfig = ~/.gitconfig
```

Keys are relative to the package — this is why the files must sit next to the ini.

### 3. Set up a new machine (restore)

```bash
cd ~/dotfiles
tomlinks restore *
```

`*` lets the shell expand to every package in the current directory, so all of them get processed in one go. Each entry is copied from the package into its destination (existing destination files are replaced).

### 4. Save your changes back (collect)

Edited some configs and want the backup updated?

```bash
cd ~/dotfiles
tomlinks collect *
```

`collect` is the inverse of `restore`: it reads the files from their system locations and writes them **into** the packages. This is how you back things up. Typical cycle:

```bash
tomlinks collect *   # snapshot current machine into packages
git add . && git commit -m "update configs"
# ... later, on another machine:
tomlinks restore *   # install everything
```

You can also name packages explicitly instead of using `*`:

```bash
tomlinks restore fish git
```

## Commands

```bash
tomlinks restore <pkg>...   # package → system: install configs
tomlinks collect <pkg>...   # system → package: backup configs
tomlinks help               # show help
```

- `<pkg>` — package directory containing `tomlinks.ini`
- Multiple packages: `tomlinks restore fish git zsh`
- All packages: `tomlinks restore *` (shell expands glob)

### Transactions

Each command runs transactionally:
- All operations are staged first
- Commit is atomic (staging directory renamed into place)
- Interrupted transactions roll back automatically on next run
- Recovery journal is written before any filesystem changes

If any file cannot be prepared, all changes are discarded.
## System configs (/etc) via sudo

Keep two separate repos — one for your user, one for root-owned system files — because `~` always expands to whoever runs the command:

```
~/dotfiles/            # user packages, run normally
~/system/              # system packages, run with sudo
├── ssh/
│   ├── tomlinks.ini
│   └── sshd_config
```

`~/system/ssh/tomlinks.ini` uses an absolute destination:

```ini
sshd_config = /etc/ssh/sshd_config
```

Then:

```bash
sudo tomlinks collect ~/system/ssh   # save system files into the package
sudo tomlinks restore ~/system/ssh   # put them back (fresh install, new server)
```
## Examples

Example packages in `examples/`:

### fish shell

```bash
cd examples
tomlinks restore fish    # install fish config to ~/.config/fish/
# edit your config...
tomlinks collect fish    # backup changes to the package
```

### git

```bash
cd examples
tomlinks restore git     # install .gitconfig and .gitignore_global to ~/
```

Each example package contains:
- `tomlinks.ini` — manifest mapping package files to system locations
- Sample config files you can customize

## Development

### Running tests

```bash
./test.sh              # all tests
./test.sh -v           # verbose
./test.sh -k restore   # filter by name
```

Tests use `uvx pytest` (installs pytest isolated, no dependency pollution).

### Test coverage

- Basic operations: restore, collect, multiple packages
- Transactions: rollback on error, recovery from crash
- Edge cases: symlinks, Unicode filenames, whitespace in paths, nested directories
- CLI: argument handling, help text
