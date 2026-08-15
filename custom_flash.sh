#!/usr/bin/env bash
#
# custom_flash.sh
#

set -e
set -o pipefail
# set -u
# set -x

sudo umount rootfs || true
sudo rm -rf rootfs
mkdir rootfs
sudo tar -xpf rootfs-lfs.tar.zst -C rootfs --numeric-owner
sudo ./tools/kernel_flash/l4t_initrd_flash.sh --external-device nvme0n1p1 \
  -c tools/kernel_flash/flash_l4t_t234_nvme.xml -p "-c bootloader/generic/cfg/flash_t234_qspi.xml" \
  --showlogs --network usb0 --erase-all jetson-orin-nano-devkit-super external
