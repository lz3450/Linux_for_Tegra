#!/usr/bin/env python3
"""Inspect and transform Ubuntu package lists with APT.

The normal entry point runs the resolver in an ephemeral Ubuntu Docker
container.  No packages are installed on the host.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
INSTALL_RE = re.compile(r"^Inst\s+(\S+)", re.MULTILINE)
PORTS_ARCHITECTURES = {"arm64", "armhf", "ppc64el", "riscv64", "s390x"}
ARCHIVE_ARCHITECTURES = {"amd64", "i386"}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def add_common_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--suite", default="noble")
        command_parser.add_argument("--architecture", default="arm64")
        command_parser.add_argument("--docker-image", default="ubuntu:24.04")
        command_parser.add_argument(
            "--no-recommends", action="store_true",
            help="exclude Recommends from dependency resolution",
        )

    roots = commands.add_parser("roots", help="reduce a closure to root packages")
    add_common_options(roots)
    roots.add_argument("--force", action="store_true")
    roots.add_argument("input", type=Path)
    roots.add_argument("output", type=Path)
    roots.set_defaults(exclude_pkglist=None, include_pkglist=None)

    filtering = commands.add_parser(
        "filter", help="exclude roots and their private dependency cascades"
    )
    add_common_options(filtering)
    filtering.add_argument("--exclude-pkglist", type=Path, required=True)
    filtering.add_argument("--include-pkglist", type=Path)
    filtering.add_argument("--force", action="store_true")
    filtering.add_argument("input", type=Path)
    filtering.add_argument("output", type=Path)

    expand = commands.add_parser("expand", help="expand roots into an install closure")
    add_common_options(expand)
    expand.add_argument("--force", action="store_true")
    expand.add_argument("input", type=Path)
    expand.add_argument("output", type=Path)

    check = commands.add_parser("check", help="check and optionally update a closure")
    add_common_options(check)
    check.add_argument("--yes", action="store_true", help="update without prompting")
    check.add_argument("input", type=Path)

    return parser.parse_args()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, text=True, **kwargs)
    except FileNotFoundError as error:
        raise SystemExit(f"required command not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error


def run_in_docker(args: argparse.Namespace) -> None:
    input_path = args.input.expanduser().resolve()
    check_mode = args.command == "check"
    exclude_path = None
    include_path = None
    if args.command == "filter":
        exclude_path = args.exclude_pkglist.expanduser().resolve()
        include_path = (
            args.include_pkglist.expanduser().resolve() if args.include_pkglist else None
        )
    script_path = Path(__file__).resolve()

    if not input_path.is_file():
        raise SystemExit(f"input package list does not exist: {input_path}")
    if exclude_path is not None and not exclude_path.is_file():
        raise SystemExit(f"exclude package list does not exist: {exclude_path}")
    if include_path is not None and not include_path.is_file():
        raise SystemExit(f"include package list does not exist: {include_path}")

    if check_mode:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{input_path.name}.closure-check.", dir=input_path.parent
        )
        os.close(descriptor)
        output_path = Path(temporary_name)
        output_path.unlink()
    else:
        output_path = args.output.expanduser().resolve()
        if output_path in {input_path, exclude_path, include_path}:
            raise SystemExit("output path must differ from all input paths")
        if output_path.exists() and not args.force:
            raise SystemExit(f"output already exists (use --force): {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

    container_output = Path("/output") / output_path.name
    shell_program = """
apt-get update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-apt >/dev/null
exec python3 /resolver.py "$@"
""".strip()
    mounts = [
        "--volume", f"{script_path}:/resolver.py:ro",
        "--volume", f"{input_path}:/input/packages:ro",
        "--volume", f"{output_path.parent}:/output",
    ]
    if exclude_path is not None:
        mounts.extend(["--volume", f"{exclude_path}:/input/exclude:ro"])
    if include_path is not None:
        mounts.extend(["--volume", f"{include_path}:/input/include:ro"])

    command = [
        "docker", "run", "--rm", *mounts,
        "--env", f"OUTPUT_UID={os.getuid()}",
        "--env", f"OUTPUT_GID={os.getgid()}",
        "--env", "PACKAGE_LIST_INTERNAL=1",
        "--env", f"DISPLAY_OUTPUT={output_path if not check_mode else '<closure-check>'}",
        args.docker_image,
        "sh", "-ceu", shell_program, "resolver",
        "expand" if check_mode else args.command,
        "--suite", args.suite,
        "--architecture", args.architecture,
        "--docker-image", args.docker_image,
    ]
    if args.no_recommends:
        command.append("--no-recommends")
    if args.command == "filter":
        command.extend(["--exclude-pkglist", "/input/exclude"])
        if include_path is not None:
            command.extend(["--include-pkglist", "/input/include"])
    command.extend(["--force", "/input/packages", str(container_output)])

    print(
        f"Running {args.command} for Ubuntu {args.suite} {args.architecture} in "
        f"{args.docker_image}...",
        flush=True,
    )
    try:
        run(command)
    except BaseException:
        if check_mode:
            output_path.unlink(missing_ok=True)
        raise
    if check_mode:
        check_closure_result(input_path, output_path, args.yes)


def check_closure_result(input_path: Path, resolved_path: Path, assume_yes: bool) -> None:
    original = read_package_list(input_path)
    resolved = read_package_list(resolved_path)
    original_set = set(original)
    resolved_set = set(resolved)
    missing = [name for name in resolved if name not in original_set]
    unexpected = [name for name in original if name not in resolved_set]

    if not missing and not unexpected:
        resolved_path.unlink()
        print(f"closure check passed: {input_path} ({len(original)} packages)")
        return

    print(f"closure check failed: {input_path}")
    if missing:
        print(f"missing packages ({len(missing)}):")
        for package in missing:
            print(f"  {package}")
    if unexpected:
        print(f"packages not selected by APT ({len(unexpected)}):")
        for package in unexpected:
            print(f"  {package}")

    update = assume_yes
    if not assume_yes:
        print(f"Update {input_path} in place? [y/N] ", end="", flush=True)
        update = sys.stdin.readline().strip().lower() in {"y", "yes"}
    if update:
        os.replace(resolved_path, input_path)
        print(f"updated: {input_path} ({len(resolved)} packages)")
        return
    resolved_path.unlink()
    print("not updated")
    raise SystemExit(1)


def configure_target_repositories(suite: str, architecture: str) -> None:
    if architecture in PORTS_ARCHITECTURES:
        archive_uri = security_uri = "http://ports.ubuntu.com/ubuntu-ports/"
    elif architecture in ARCHIVE_ARCHITECTURES:
        archive_uri = "http://archive.ubuntu.com/ubuntu/"
        security_uri = "http://security.ubuntu.com/ubuntu/"
    else:
        supported = sorted(PORTS_ARCHITECTURES | ARCHIVE_ARCHITECTURES)
        raise SystemExit(
            f"unsupported Ubuntu architecture {architecture!r}; "
            f"choose one of: {', '.join(supported)}"
        )

    sources_dir = Path("/etc/apt/sources.list.d")
    for source_file in sources_dir.iterdir():
        if source_file.suffix in {".list", ".sources"}:
            source_file.unlink()
    Path("/etc/apt/sources.list").write_text("", encoding="utf-8")
    source_text = f"""Types: deb
URIs: {archive_uri}
Suites: {suite} {suite}-updates {suite}-backports
Components: main universe restricted multiverse
Architectures: {architecture}
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: {security_uri}
Suites: {suite}-security
Components: main universe restricted multiverse
Architectures: {architecture}
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""
    (sources_dir / "target.sources").write_text(source_text, encoding="utf-8")

    run(["dpkg", "--add-architecture", architecture])
    run(
        [
            "apt-get",
            "-o", f"APT::Architecture={architecture}",
            "-o", f"APT::Architectures::={architecture}",
            "update",
        ],
        stdout=subprocess.DEVNULL,
    )


def read_package_list(path: Path) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not PACKAGE_RE.fullmatch(line):
            raise SystemExit(
                f"{path}:{line_number}: expected one unversioned package name, got {line!r}"
            )
        if line in seen:
            raise SystemExit(f"{path}:{line_number}: duplicate package {line!r}")
        seen.add(line)
        packages.append(line)
    if not packages:
        raise SystemExit(f"input package list is empty: {path}")
    return packages


def load_apt_cache(architecture: str):
    try:
        import apt_pkg
    except ImportError as error:
        raise SystemExit("python3-apt is required in internal resolver mode") from error

    Path("/tmp/empty-dpkg-status").write_text("", encoding="utf-8")
    apt_pkg.init_config()
    apt_pkg.config.set("APT::Architecture", architecture)
    apt_pkg.config.set("APT::Architectures::", architecture)
    apt_pkg.config.set("Dir::State::status", "/tmp/empty-dpkg-status")
    apt_pkg.init_system()
    cache = apt_pkg.Cache()
    return apt_pkg, cache, apt_pkg.DepCache(cache)


def candidate(cache, depcache, name: str, architecture: str):
    for key in (name, f"{name}:{architecture}"):
        if key in cache:
            version = depcache.get_candidate_ver(cache[key])
            if version is not None:
                return version
    return None


def named_dependency_targets(version, listed: set[str], include_recommends: bool) -> set[str]:
    kinds = ["PreDepends", "Depends"]
    if include_recommends:
        kinds.append("Recommends")
    targets: set[str] = set()
    for kind in kinds:
        for alternatives in version.depends_list.get(kind, []):
            for dependency in alternatives:
                # An OR group installs one alternative, not all of them.  Follow
                # APT's normal preference for the first satisfiable alternative.
                if not dependency.all_targets():
                    continue
                target = dependency.target_pkg.name.split(":", 1)[0]
                if target in listed:
                    targets.add(target)
                break
    return targets


def apt_install_closure(roots: list[str], architecture: str, include_recommends: bool) -> set[str]:
    command = [
        "apt-get", "-s",
        "-o", f"APT::Architecture={architecture}",
        "-o", f"APT::Architectures::={architecture}",
        "-o", "Dir::State::status=/tmp/empty-dpkg-status",
        "-o", f"APT::Install-Recommends={'true' if include_recommends else 'false'}",
        "install", *roots,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode:
        tail = "\n".join(result.stdout.splitlines()[-80:])
        raise SystemExit("APT simulation failed:\n" + tail)
    return {
        match.group(1).split(":", 1)[0]
        for match in INSTALL_RE.finditer(result.stdout)
    }


def source_component_seeds(nodes: list[str], edges: dict[str, set[str]]) -> list[str]:
    """Choose one seed from each source SCC in the induced dependency graph."""
    node_set = set(nodes)
    node_order = {name: position for position, name in enumerate(nodes)}
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    next_index = 0

    def visit(node: str) -> None:
        nonlocal next_index
        indices[node] = lowlinks[node] = next_index
        next_index += 1
        stack.append(node)
        on_stack.add(node)

        for target in edges.get(node, set()) & node_set:
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(component)

    for node in nodes:
        if node not in indices:
            visit(node)

    component_of = {
        member: component_index
        for component_index, component in enumerate(components)
        for member in component
    }
    has_incoming = set()
    for source in nodes:
        source_component = component_of[source]
        for target in edges.get(source, set()) & node_set:
            target_component = component_of[target]
            if source_component != target_component:
                has_incoming.add(target_component)

    seeds = [
        min(component, key=node_order.__getitem__)
        for component_index, component in enumerate(components)
        if component_index not in has_incoming
    ]
    return sorted(seeds, key=node_order.__getitem__)


def validated_roots(
    packages: list[str],
    edges: dict[str, set[str]],
    architecture: str,
    include_recommends: bool,
) -> tuple[list[str], int, int]:
    depended_on = set().union(*edges.values()) if edges else set()
    roots = [name for name in packages if name not in depended_on]
    initial_root_count = len(roots)
    passes = 0

    while True:
        passes += 1
        selected = apt_install_closure(roots, architecture, include_recommends)
        uncovered = [name for name in packages if name not in selected]
        print(
            f"closure pass {passes}: roots={len(roots)}, "
            f"covered={len(packages) - len(uncovered)}/{len(packages)}",
            flush=True,
        )
        if not uncovered:
            break

        additions = source_component_seeds(uncovered, edges)
        if not additions:
            raise SystemExit("could not select roots for the uncovered dependency graph")
        # Keep every APT check in the same order that will be written to output.
        root_set = set(roots)
        root_set.update(additions)
        roots = [name for name in packages if name in root_set]
        if passes > len(packages):
            raise SystemExit("dependency closure did not converge")

    selected = apt_install_closure(roots, architecture, include_recommends)
    uncovered = [name for name in packages if name not in selected]
    if uncovered:
        raise SystemExit("final validation failed; uncovered packages:\n" + "\n".join(uncovered))
    return roots, initial_root_count, passes


def dependency_closure(seeds: set[str], edges: dict[str, set[str]]) -> set[str]:
    closure = set(seeds)
    pending = list(seeds)
    while pending:
        package = pending.pop()
        for dependency in edges.get(package, set()):
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def compute_removable(
    explicit_excludes: set[str],
    edges: dict[str, set[str]],
    protected: set[str] | None = None,
) -> set[str]:
    """Return excludes plus unshared dependencies, minus protected closures."""
    descendants = dependency_closure(explicit_excludes, edges)

    reverse_edges = {package: set() for package in edges}
    for package, dependencies in edges.items():
        for dependency in dependencies:
            reverse_edges.setdefault(dependency, set()).add(package)

    removable = set(descendants)
    protect = [
        package
        for package in descendants - explicit_excludes
        if not reverse_edges.get(package, set()) <= removable
    ]
    while protect:
        package = protect.pop()
        if package not in removable or package in explicit_excludes:
            continue
        removable.remove(package)
        for dependency in edges.get(package, set()):
            if dependency in removable and dependency not in explicit_excludes:
                protect.append(dependency)
    return removable - (protected or set())


def write_output(path: Path, packages: list[str], source_mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary_path = Path(stream.name)
        stream.write("\n".join(packages) + "\n")
    os.chmod(temporary_path, source_mode)
    os.replace(temporary_path, path)
    uid = int(os.environ.get("OUTPUT_UID", "0"))
    gid = int(os.environ.get("OUTPUT_GID", "0"))
    os.chown(path, uid, gid)


def resolve_roots_or_filter(args: argparse.Namespace) -> None:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    packages = read_package_list(input_path)
    listed = set(packages)
    include_recommends = not args.no_recommends
    configure_target_repositories(args.suite, args.architecture)
    _apt_pkg, cache, depcache = load_apt_cache(args.architecture)

    versions = {}
    missing = []
    for name in packages:
        version = candidate(cache, depcache, name, args.architecture)
        if version is None:
            missing.append(name)
        else:
            versions[name] = version
    if missing:
        raise SystemExit(
            f"{len(missing)} packages are unavailable for {args.suite}/{args.architecture}:\n"
            + "\n".join(missing)
        )

    edges: dict[str, set[str]] = {}
    edge_count = 0
    for source, version in versions.items():
        targets = named_dependency_targets(version, listed, include_recommends)
        targets.discard(source)
        edges[source] = targets
        edge_count += len(targets)

    roots, initial_root_count, _passes = validated_roots(
        packages, edges, args.architecture, include_recommends
    )

    source_mode = stat.S_IMODE(input_path.stat().st_mode)
    if args.command == "roots":
        write_output(output_path, roots, source_mode)
        print(f"input packages:       {len(packages)}")
        print(f"dependency edges:     {edge_count}")
        print(f"initial graph roots:  {initial_root_count}")
        print(f"validated roots:      {len(roots)}")
        print(f"dependencies removed: {len(packages) - len(roots)}")
    else:
        requested = read_package_list(args.exclude_pkglist.resolve())
        requested_includes = (
            read_package_list(args.include_pkglist.resolve())
            if args.include_pkglist is not None
            else []
        )
        root_set = set(roots)
        absent = [name for name in requested if name not in listed]
        non_roots = [name for name in requested if name in listed and name not in root_set]
        absent_includes = [name for name in requested_includes if name not in listed]
        include_seeds = listed & set(requested_includes)
        protected = dependency_closure(include_seeds, edges)
        protected_excludes = [
            name for name in requested
            if name in root_set and name in protected
        ]
        eligible = (root_set & set(requested)) - protected
        if absent:
            print(
                "WARNING: exclude packages absent from input; ignored: "
                + " ".join(absent),
                file=sys.stderr,
            )
        if absent_includes:
            print(
                "WARNING: include packages absent from input; ignored: "
                + " ".join(absent_includes),
                file=sys.stderr,
            )
        if non_roots:
            print(
                "WARNING: exclude packages are not root packages; ignored: "
                + " ".join(non_roots),
                file=sys.stderr,
            )
        if protected_excludes:
            print(
                "WARNING: exclude packages protected by include list; ignored: "
                + " ".join(protected_excludes),
                file=sys.stderr,
            )

        removable = compute_removable(eligible, edges, protected)
        output_packages = [name for name in packages if name not in removable]
        for package in removable - eligible:
            outside_dependents = [
                parent
                for parent, dependencies in edges.items()
                if package in dependencies and parent not in removable
            ]
            if outside_dependents:
                raise SystemExit(
                    f"internal error: removing shared dependency {package}; "
                    f"still required by {', '.join(outside_dependents)}"
                )

        # The input may be an incomplete or stale closure. Complete the filtered
        # result with the packages selected by current APT metadata so that root
        # extraction can reproduce it exactly.
        selected_output = apt_install_closure(
            output_packages, args.architecture, include_recommends
        )
        required_excludes = eligible & selected_output
        if required_excludes:
            raise SystemExit(
                "remaining packages still require explicitly excluded roots:\n"
                + "\n".join(sorted(required_excludes))
            )
        closure_additions = selected_output - set(output_packages)
        output_packages = sorted(selected_output)
        write_output(output_path, output_packages, source_mode)
        print(f"input packages:             {len(packages)}")
        print(f"validated roots:            {len(roots)}")
        print(f"requested excludes:         {len(requested)}")
        print(f"eligible root excludes:     {len(eligible)}")
        print(f"requested includes:         {len(requested_includes)}")
        print(f"protected package closure:  {len(protected)}")
        print(
            f"ignored excludes:           "
            f"{len(non_roots) + len(absent) + len(protected_excludes)}"
        )
        print(f"ignored absent includes:    {len(absent_includes)}")
        print(f"cascade dependencies:       {len(removable - eligible)}")
        print(f"closure packages added:     {len(closure_additions)}")
        print(f"output packages:            {len(output_packages)}")
    print(f"output: {os.environ.get('DISPLAY_OUTPUT', str(output_path))}")


def resolve_expand(args: argparse.Namespace) -> None:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    requested = read_package_list(input_path)
    include_recommends = not args.no_recommends

    configure_target_repositories(args.suite, args.architecture)
    _apt_pkg, cache, depcache = load_apt_cache(args.architecture)
    missing = [
        name
        for name in requested
        if candidate(cache, depcache, name, args.architecture) is None
    ]
    if missing:
        raise SystemExit(
            f"{len(missing)} packages are unavailable for "
            f"{args.suite}/{args.architecture}:\n" + "\n".join(missing)
        )

    installed = apt_install_closure(requested, args.architecture, include_recommends)
    uncovered = [name for name in requested if name not in installed]
    if uncovered:
        raise SystemExit("APT did not select requested packages:\n" + "\n".join(uncovered))

    output_packages = sorted(installed)
    source_mode = stat.S_IMODE(input_path.stat().st_mode)
    write_output(output_path, output_packages, source_mode)
    print(f"requested packages: {len(requested)}")
    print(f"packages to install: {len(output_packages)}")
    print(f"output: {os.environ.get('DISPLAY_OUTPUT', str(output_path))}")


def main() -> None:
    args = parse_arguments()
    if os.environ.get("PACKAGE_LIST_INTERNAL") == "1":
        if args.command in {"roots", "filter"}:
            resolve_roots_or_filter(args)
        elif args.command == "expand":
            resolve_expand(args)
        else:
            raise SystemExit("check is a host-only command")
    else:
        run_in_docker(args)


if __name__ == "__main__":
    main()
