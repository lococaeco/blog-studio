#!/usr/bin/env python3
"""Build one self-contained executable on the OS where it will be used."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE


def collect_notices(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'LICENSE', destination / 'Blog-Studio-LICENSE.txt')
    original_license = ROOT / 'docs' / 'original-repository-license.txt'
    if original_license.is_file():
        shutil.copy2(original_license, destination / 'Original-Repository-LICENSE.txt')
    for package in ('PyYAML', 'PyInstaller', 'pyinstaller-hooks-contrib'):
        dist = importlib.metadata.distribution(package)
        for entry in dist.files or []:
            if any(word in entry.name.upper() for word in ('LICENSE', 'COPYING', 'COPYRIGHT')):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    shutil.copy2(source, destination / f'{package}-{entry.name}')
    for folder in (Path(sys.base_prefix), Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}'):
        for name in ('LICENSE', 'LICENSE.txt', 'LICENSE.rst'):
            if (folder / name).is_file():
                shutil.copy2(folder / name, destination / 'Python-LICENSE.txt')
                break
    (destination / 'README.txt').write_text(
        'Blog Studio includes Python, PyYAML, Tcl/Tk, and a PyInstaller bootloader.\n'
        'PyInstaller licensing includes an exception for generated applications.\n'
        'Editor library licenses are bundled in static/vendor/licenses/.\n'
        'Tcl/Tk license terms are included in the bundled Tcl/Tk library data.\n'
        'Source and build instructions: https://github.com/lococaeco/blog-studio\n',
        encoding='utf-8',
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=HERE / 'dist')
    args = parser.parse_args()
    if sys.platform not in ('linux', 'win32') or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('Build on x86_64 Windows or Linux; cross-compilation is not supported.')
    # Fail before shipping an executable whose first GUI launch would fail.
    import tkinter
    tkinter.Tcl().eval('info patchlevel')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = HERE / '.build'
    notices = build / 'licenses'
    collect_notices(notices)
    name = 'BlogStudio' if sys.platform == 'win32' else 'BlogStudio-linux-x86_64'
    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
        '--windowed', '--noupx', '--name', name, '--paths', str(HERE),
        '--distpath', str(output), '--workpath', str(build / 'work'),
        '--specpath', str(build), '--add-data', f'{HERE / "static"}:static',
        '--add-data', f'{notices}:licenses',
        '--add-data', f'{HERE / "packaging" / "icon.png"}:.',
        '--hidden-import', 'tkinter', '--hidden-import', 'tkinter.ttk',
        '--hidden-import', 'tkinter.filedialog', '--hidden-import', 'tkinter.messagebox',
    ]
    if sys.platform == 'win32':
        command += ['--icon', str(HERE / 'packaging' / 'icon.ico')]
    command.append(str(HERE / 'desktop.py'))
    subprocess.run(command, cwd=HERE, check=True)
    executable = output / (name + ('.exe' if sys.platform == 'win32' else ''))
    checksum = hashlib.sha256(executable.read_bytes()).hexdigest()
    (output / (executable.name + '.sha256')).write_text(f'{checksum}  {executable.name}\n', encoding='ascii')
    from updater import CURRENT_VERSION
    info = {'version': CURRENT_VERSION, 'file': executable.name, 'sha256': checksum, 'bytes': executable.stat().st_size,
            'os': sys.platform, 'architecture': platform.machine(), 'python': platform.python_version(),
            'pyinstaller': importlib.metadata.version('PyInstaller'), 'libc': platform.libc_ver()}
    (output / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(info, indent=2))


if __name__ == '__main__':
    main()
