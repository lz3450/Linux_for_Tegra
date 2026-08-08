# Ubuntu package-list tools

`package_list.py` analyzes Ubuntu package lists with APT in an isolated Docker
container. It defaults to Ubuntu Noble and ARM64, uses an empty target package
status, and does not modify the host package database.

Docker and network access are required. Use the same dependency options—most
importantly `--no-recommends`—throughout one workflow.

## Commands

- `filter`: remove selected root packages and their private dependency
  cascades. Only validated roots can be excluded. `--include-pkglist` protects
  listed packages and all their dependencies. Shared dependencies are kept.
- `roots`: reduce an installation closure to packages needed as explicit APT
  seeds.
- `expand`: simulate installing seeds and write every package APT selects.
- `check`: verify that a list is an APT closure. If incomplete, show missing
  packages and offer to update the file in place. Add `--yes` to skip the
  prompt.

## Complete workflow

```bash
./package_list.py filter \
  --exclude-pkglist nvubuntu-noble-excluded-aarch64-packages \
  --include-pkglist nvubuntu-noble-included-aarch64-packages \
  --no-recommends --force \
  nvubuntu-noble-desktop-aarch64-packages \
  nvubuntu-noble-custom-aarch64-packages

./package_list.py roots \
  --no-recommends --force \
  nvubuntu-noble-custom-aarch64-packages \
  nvubuntu-noble-lfs-aarch64-packages

./package_list.py expand \
  --no-recommends --force \
  nvubuntu-noble-lfs-aarch64-packages \
  nvubuntu-noble-custom-aarch64-packages.new

cmp nvubuntu-noble-custom-aarch64-packages \
    nvubuntu-noble-custom-aarch64-packages.new
```

The final comparison should be identical.

## Check or repair a closure

```bash
./package_list.py check \
  --no-recommends \
  nvubuntu-noble-desktop-aarch64-packages
```

For a non-interactive update:

```bash
./package_list.py check \
  --no-recommends --yes \
  nvubuntu-noble-desktop-aarch64-packages
```

Common overrides are `--suite`, `--architecture`, and `--docker-image`.
