#!/usr/bin/env bash
#
# custom_flash.sh
#

set -e
set -o pipefail
# set -u
# set -x

pushd tools/samplefs
./package_list.py filter \
    --force \
    --architecture arm64 \
    --exclude-pkglist nvubuntu-noble-excluded-aarch64-packages \
    --include-pkglist nvubuntu-noble-included-aarch64-packages \
    nvubuntu-noble-desktop-aarch64-packages \
    nvubuntu-noble-custom-aarch64-packages

./package_list.py roots \
    --force \
    --architecture arm64 \
    nvubuntu-noble-custom-aarch64-packages \
    nvubuntu-noble-lfs-aarch64-packages

sudo ./nv_build_samplefs.sh --abi aarch64 --distro ubuntu --flavor lfs --version noble --verbose
popd

sudo umount rootfs || true
sudo rm -rf rootfs
mkdir rootfs
sudo tar -xpf tools/samplefs/sample_fs.tbz2 -C rootfs --numeric-owner
sudo ./apply_binaries.sh
sudo tar --zstd -cpf rootfs-lfs.tar.zst rootfs --numeric-owner

rsync -av ~/Projects/jetson/orin_nano/Linux_for_Tegra/rootfs-lfs.tar.zst LAB:Projects/jetson/orin_nano/Linux_for_Tegra/
