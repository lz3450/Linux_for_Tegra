#!/usr/bin/env python3
"""Expand package seeds into the complete APT installation closure."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat

import nv_clean_package_list as cleaner


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve package seeds into all packages APT would install in an "
            "empty target filesystem."
        ),
        epilog="example: get_package_to_be_installed.py roots closure --force",
    )
    parser.add_argument("input", type=Path, help="package seed list")
    parser.add_argument("output", type=Path, help="resolved installation closure")
    parser.add_argument(
        "--suite", default="noble", help="Ubuntu suite/codename (default: noble)"
    )
    parser.add_argument(
        "--architecture", default="arm64", help="target architecture (default: arm64)"
    )
    parser.add_argument(
        "--docker-image",
        default="ubuntu:24.04",
        help="resolver image (default: ubuntu:24.04)",
    )
    parser.add_argument(
        "--no-recommends",
        action="store_true",
        help="exclude Recommends from the installation closure",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output file"
    )
    parser.add_argument("--internal-resolve", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def run_in_docker(args: argparse.Namespace) -> None:
    input_path = args.input.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    tools_path = Path(__file__).resolve().parent

    if not input_path.is_file():
        raise SystemExit(f"input package list does not exist: {input_path}")
    if input_path == output_path:
        raise SystemExit("input and output paths must differ")
    if output_path.exists() and not args.force:
        raise SystemExit(f"output already exists (use --force): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    container_output = Path("/output") / output_path.name
    shell_program = """
apt-get update >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-apt >/dev/null
exec python3 /tools/get_package_to_be_installed.py --internal-resolve "$@"
""".strip()
    command = [
        "docker", "run", "--rm",
        "--volume", f"{tools_path}:/tools:ro",
        "--volume", f"{input_path}:/input/packages:ro",
        "--volume", f"{output_path.parent}:/output",
        "--env", f"OUTPUT_UID={os.getuid()}",
        "--env", f"OUTPUT_GID={os.getgid()}",
        "--env", f"DISPLAY_OUTPUT={output_path}",
        args.docker_image,
        "sh", "-ceu", shell_program, "resolver",
        "/input/packages", str(container_output),
        "--suite", args.suite,
        "--architecture", args.architecture,
        "--docker-image", args.docker_image,
        "--force",
    ]
    if args.no_recommends:
        command.append("--no-recommends")

    print(
        f"Resolving Ubuntu {args.suite} {args.architecture} installation closure "
        f"in {args.docker_image}...",
        flush=True,
    )
    cleaner.run(command)


def resolve(args: argparse.Namespace) -> None:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    requested = cleaner.read_package_list(input_path)
    include_recommends = not args.no_recommends

    cleaner.configure_target_repositories(args.suite, args.architecture)
    _apt_pkg, cache, depcache = cleaner.load_apt_cache(args.architecture)

    missing = [
        name
        for name in requested
        if cleaner.candidate(cache, depcache, name, args.architecture) is None
    ]
    if missing:
        raise SystemExit(
            f"{len(missing)} packages are unavailable for "
            f"{args.suite}/{args.architecture}:\n" + "\n".join(missing)
        )

    installed = cleaner.apt_install_closure(
        requested, args.architecture, include_recommends
    )
    uncovered_requests = [name for name in requested if name not in installed]
    if uncovered_requests:
        raise SystemExit(
            "APT did not select requested packages:\n" + "\n".join(uncovered_requests)
        )

    output_packages = sorted(installed)
    source_mode = stat.S_IMODE(input_path.stat().st_mode)
    cleaner.write_output(output_path, output_packages, source_mode)
    print(f"requested packages: {len(requested)}")
    print(f"packages to install: {len(output_packages)}")
    print(f"output: {os.environ.get('DISPLAY_OUTPUT', str(output_path))}")


def main() -> None:
    args = parse_arguments()
    if args.internal_resolve:
        resolve(args)
    else:
        run_in_docker(args)


if __name__ == "__main__":
    main()
