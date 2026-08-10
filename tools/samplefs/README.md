# Ubuntu package-list tools

`package_list.py` analyzes Ubuntu package lists using isolated APT metadata. It
defaults to Ubuntu Noble, AMD64, and the Docker backend. It uses an empty target
package status and does not modify the host package database.

The self-contained script builds the default
`package-list-resolver:ubuntu-24.04` image automatically when missing. Use
`--backend native` to run without Docker; it requires `apt-get`, `python3-apt`,
`ubuntu-keyring`, and network access. Recommends are excluded by default; use
`--recommends` to include them. Use the same dependency options throughout one
workflow.

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
./package_list.py expand original_pkglist

# Exclude selected packages
./package_list.py filter --exclude-pkglist excluded-packages --include-pkglist included-packages --force original_pkglist filtered_pkglist

# Remove all dependency packages
./package_list.py roots --force filtered_pkglist target_pkglist

```

## Check a closure

```bash
./package_list.py check original_pkglist
```

Expand or repair it in place:

```bash
./package_list.py expand original_pkglist
```

Common overrides are `--suite`, `--architecture`, and `--backend`. Docker also
accepts `--docker-image`.

For ARM64 resolution in Docker:

```bash
./package_list.py check \
  --architecture arm64 \
  nvubuntu-noble-desktop-aarch64-packages
```
