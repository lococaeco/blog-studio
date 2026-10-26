#!/usr/bin/env python3
"""Package the Linux one-file executable as a genuine type-2 AppImage.

Build the input on Ubuntu 22.04 with build_desktop.py first. Only the two
versioned, checksum-verified official AppImage binaries below are downloaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
APPIMAGE_NAME = 'BlogStudio-x86_64.AppImage'
TOOLCHAIN = {
    'appimagetool': {
        'version': '1.9.1',
        'url': 'https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage',
        'sha256': 'ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0',
        'release': 'https://github.com/AppImage/appimagetool/releases/tag/1.9.1',
        'filename': 'appimagetool-1.9.1-x86_64.AppImage',
    },
    'runtime': {
        'version': '20251108',
        'url': 'https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64',
        'sha256': '2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d',
        'release': 'https://github.com/AppImage/type2-runtime/releases/tag/20251108',
        'filename': 'runtime-20251108-x86_64',
    },
}


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def checked_download(spec: dict, cache: Path) -> Path:
    """Never execute a downloaded or cached tool before checking its pinned hash."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / spec['filename']
    if target.exists():
        if sha256(target) != spec['sha256']:
            raise RuntimeError(f'Cached tool checksum mismatch: {target}. Remove it and retry.')
        target.chmod(0o755)
        return target
    request = Request(spec['url'], headers={'User-Agent': 'BlogStudio-AppImage-build/1.0'})
    with tempfile.NamedTemporaryFile(prefix='.download-', dir=cache, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            print(f"Downloading official {spec['filename']}", flush=True)
            with urlopen(request, timeout=60) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        raise RuntimeError('Unexpectedly large AppImage toolchain download.')
                    stream.write(chunk)
            stream.flush()
            if sha256(temporary) != spec['sha256']:
                raise RuntimeError(f"Downloaded tool checksum mismatch: {spec['filename']}")
            temporary.chmod(0o755)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def validate_linux_binary(binary: Path) -> None:
    with binary.open('rb') as stream:
        header = stream.read(64)
    if (len(header) < 64 or header[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', header, 18)[0] != 62):
        raise ValueError('Input must be the Linux x86_64 ELF produced by build_desktop.py.')
    if header[8:11] == b'AI\x02':
        raise ValueError('Input is already an AppImage; provide the plain Linux executable.')


def prepare_appdir(appdir: Path, binary: Path) -> dict:
    executable = appdir / 'usr/bin/BlogStudio'
    executable.parent.mkdir(parents=True)
    shutil.copy2(binary, executable)
    executable.chmod(0o755)
    shutil.copy2(HERE / 'packaging/AppRun', appdir / 'AppRun')
    (appdir / 'AppRun').chmod(0o755)
    shutil.copy2(HERE / 'packaging/blog-studio.desktop', appdir / 'blog-studio.desktop')
    applications = appdir / 'usr/share/applications'
    applications.mkdir(parents=True)
    (applications / 'blog-studio.desktop').symlink_to('../../../blog-studio.desktop')
    shutil.copy2(HERE / 'packaging/icon.png', appdir / 'blog-studio.png')
    (appdir / '.DirIcon').symlink_to('blog-studio.png')
    icon_size = struct.unpack('>II', (HERE / 'packaging/icon.png').read_bytes()[16:24])
    icons = appdir / f'usr/share/icons/hicolor/{icon_size[0]}x{icon_size[1]}/apps'
    icons.mkdir(parents=True)
    shutil.copy2(HERE / 'packaging/icon.png', icons / 'blog-studio.png')
    notices = appdir / 'usr/share/doc/blog-studio'
    notices.mkdir(parents=True)
    shutil.copy2(HERE / 'LICENSE', notices / 'LICENSE')
    shutil.copy2(HERE / 'packaging/AppImage-runtime-LICENSE.txt', notices / 'AppImage-runtime-LICENSE.txt')
    shutil.copy2(HERE / 'packaging/AppImage-toolchain.md', notices / 'AppImage-toolchain.md')
    provenance = {
        'input_file': binary.name, 'input_sha256': sha256(binary),
        'source': 'https://github.com/lococaeco/blog-studio', 'toolchain': TOOLCHAIN,
    }
    source_info = binary.parent / 'build-info.json'
    if source_info.is_file():
        info = json.loads(source_info.read_text('utf-8'))
        # Never attach metadata belonging to a different file or build.
        if info.get('file') == binary.name and info.get('sha256') == provenance['input_sha256']:
            provenance['input_build'] = info
    (notices / 'build-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', 'utf-8')
    return provenance


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=HERE / 'dist/BlogStudio-linux-x86_64')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'dist')
    parser.add_argument('--cache-dir', type=Path, default=HERE / '.build/appimage-tools')
    args = parser.parse_args(argv)
    if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        parser.error('Build AppImages on x86_64 Linux; the input executable should be built on Ubuntu 22.04.')
    try:
        binary = args.binary.resolve(strict=True)
        validate_linux_binary(binary)
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        cache = args.cache_dir.resolve()
        tool = checked_download(TOOLCHAIN['appimagetool'], cache)
        runtime = checked_download(TOOLCHAIN['runtime'], cache)
        build = HERE / '.build'
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='appimage-', dir=build) as folder:
            work = Path(folder)
            appdir = work / 'BlogStudio.AppDir'
            provenance = prepare_appdir(appdir, binary)
            result = work / APPIMAGE_NAME
            env = os.environ.copy()
            env.update(ARCH='x86_64', APPIMAGE_EXTRACT_AND_RUN='1')
            # An explicit runtime prevents appimagetool from downloading a
            # moving "latest" runtime behind our pinned toolchain.
            subprocess.run([str(tool), '--no-appstream', '--runtime-file', str(runtime),
                            str(appdir), str(result)], env=env, check=True)
            with result.open('rb') as stream:
                header = stream.read(64)
            if header[:4] != b'\x7fELF' or header[8:11] != b'AI\x02':
                raise RuntimeError('appimagetool did not produce a type-2 AppImage.')
            result.chmod(0o755)
            destination = output / APPIMAGE_NAME
            checksum = sha256(result)
            info = {**provenance, 'file': APPIMAGE_NAME, 'format': 'AppImage type 2',
                    'architecture': 'x86_64', 'sha256': checksum, 'bytes': result.stat().st_size}
            os.replace(result, destination)
        (output / (APPIMAGE_NAME + '.sha256')).write_text(f'{checksum}  {APPIMAGE_NAME}\n', 'ascii')
        (output / 'appimage-build-info.json').write_text(json.dumps(info, indent=2) + '\n', 'utf-8')
        print(json.dumps(info, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'AppImage build failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
