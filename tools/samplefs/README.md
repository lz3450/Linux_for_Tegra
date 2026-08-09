# Ubuntu package-list tools

`package_list.py` analyzes Ubuntu package lists with APT in an isolated Docker
container. It defaults to Ubuntu Noble and ARM64, uses an empty target package
status, and does not modify the host package database.

Docker and network access are required. Use the same dependency options—most
importantly `--no-recommends`—throughout one workflow.

## Commands

- `filter`: optionally remove selected root packages and their private
  dependency cascades. Only validated roots can be excluded.
  `--include-pkglist` protects listed packages and all their dependencies.
  Shared dependencies are kept.
- `roots`: reduce an installation closure to packages needed as explicit APT
  seeds.
- `expand`: simulate installing seeds and update the input package list with
  every package APT selects.
- `check`: report whether a package list is already a closure. It makes no
  changes and exits with status 0 when complete or 1 when incomplete.

## Complete workflow

```bash

# Optional
./package_list.py expand --no-recommends original_pkglist

# Exclude selected packages
./package_list.py filter --exclude-pkglist excluded-packages --include-pkglist included-packages --no-recommends --force original_pkglist filtered_pkglist

# Remove all dependency packages
./package_list.py roots --no-recommends --force filtered_pkglist target_pkglist

```

## Check a closure

```bash
./package_list.py check --no-recommends original_pkglist
```

Expand or repair it in place:

```bash
./package_list.py expand --no-recommends original_pkglist
```

Common overrides are `--suite`, `--architecture`, and `--docker-image`.
