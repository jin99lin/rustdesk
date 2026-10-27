#!/bin/bash
# Adoluna Support .deb packager. Run from repo root after:
#   cargo build --locked --lib --release --features hwcodec,flutter,unix-file-copy-paste
#   (cd flutter && flutter build linux --release)
set -e
VERSION=${1:-1.5.0}
R=res/adoluna/linux
B=flutter/build/linux/x64/release/bundle
T=$(mktemp -d)/pkg
mkdir -p $T/DEBIAN $T/usr/share/adoluna-support/files/systemd $T/usr/share/icons/hicolor/256x256/apps $T/usr/share/icons/hicolor/scalable/apps $T/usr/share/applications
cp -r $B/* $T/usr/share/adoluna-support/
cp $R/adolunasupport.service $T/usr/share/adoluna-support/files/systemd/
cp $R/adoluna-support-256.png $T/usr/share/icons/hicolor/256x256/apps/adoluna-support.png
cp $R/adoluna-support.svg $T/usr/share/icons/hicolor/scalable/apps/adoluna-support.svg
cp $R/adoluna-support.desktop $R/adoluna-support-link.desktop $T/usr/share/applications/
cp LICENCE $T/usr/share/adoluna-support/LICENCE
cp $R/DEBIAN/postinst $R/DEBIAN/prerm $R/DEBIAN/postrm $T/DEBIAN/
cat > $T/DEBIAN/control <<CTL
Package: adoluna-support
Section: net
Priority: optional
Version: $VERSION
Architecture: amd64
Maintainer: Adoluna <dev@adoluna.com>
Homepage: https://adolunapos.com
Depends: libgtk-3-0t64 | libgtk-3-0, libxcb-randr0, libxdo3 | libxdo4, libxfixes3, libxcb-shape0, libxcb-xfixes0, libasound2t64 | libasound2, libsystemd0, curl, libva2, libva-drm2, libva-x11-2, libgstreamer-plugins-base1.0-0, gstreamer1.0-pipewire
Recommends: libayatana-appindicator3-1
Description: Adoluna Support remote assistance (based on RustDesk, AGPL-3.0).
CTL
(cd $T && find usr -type f -exec md5sum {} + > DEBIAN/md5sums)
fakeroot dpkg-deb -Zxz -b $T adoluna-support-$VERSION-x86_64.deb
rm -rf $(dirname $T)
