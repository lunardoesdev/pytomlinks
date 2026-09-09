# pytomlinks

rewrite of tomlinks so a single-file python script

Simple backup/restore tool for package-based dotfiles.

The idea: your configs are split into **backup packages** — plain directories that hold both the backed-up files and a small `tomlinks.ini` manifest saying where each file belongs on the system. No symlinks, no daemon, no database. Just INI and copies.

## Installation

```bash
./install.sh
```

Or just run the script: `./tomlinks`.

## The concept: backup packages

A backup package is any directory containing a `tomlinks.ini`. The backed-up files sit **inside** the package, next to the manifest:

```
fish/
├── tomlinks.ini      <- the manifest
└── config.fish      <- the backed-up file itself
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

| Command | Direction | Description |
|---|---|---|
| `tomlinks restore <pkg>...` | package → system | Copy each file from the package to its destination |
| `tomlinks collect <pkg>...` | system → package | Copy each destination file back into the package (backup) |
| `tomlinks help` | — | Show help |

Notes:

- `<pkg>` arguments are package directories containing `tomlinks.ini`; several may be given at once (`tomlinks restore fish git zsh`)
- `restore *` / `collect *` rely on shell globbing — run it from your dotfiles root to hit every package
- `restore` replaces whatever is at the destination before copying
- if a source file listed in the ini is missing, it's skipped with an error message

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

Your user packages stay untouched by sudo runs, and vice versa.

## Examples

See [`examples/`](examples/) — a minimal working set:

```
examples/
├── fish/           # config.fish -> ~/.config/fish/config.fish
└── git/            # gitconfig   -> ~/.gitconfig
```

Try it out safely:

```bash
cd examples
../tomlinks collect fish    # pulls your real fish config into the example package
```
