# AppImage toolchain

Blog Studio uses the official AppImage project tools below. Their release asset
SHA-256 digests are pinned in `build_appimage.py` and checked before execution,
including on a cache hit. The input remains available as a plain Linux binary.

| Component | Release | SHA-256 |
| --- | --- | --- |
| appimagetool, x86_64 | [1.9.1](https://github.com/AppImage/appimagetool/releases/tag/1.9.1) | `ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0` |
| type-2 runtime, x86_64 | [20251108](https://github.com/AppImage/type2-runtime/releases/tag/20251108) | `2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d` |

Digest provenance: the `digest` field of each asset in the official GitHub
[appimagetool release API](https://api.github.com/repos/AppImage/appimagetool/releases/tags/1.9.1)
and [runtime release API](https://api.github.com/repos/AppImage/type2-runtime/releases/tags/20251108).
Checked on 2026-10-26. Download URLs point to these versioned releases, never a
moving `continuous` or `latest` release. `--runtime-file` disables appimagetool's
automatic runtime download.

The embedded runtime's upstream license is included alongside this document as
`AppImage-runtime-LICENSE.txt`. It includes notices for its statically linked
libraries. The corresponding upstream source and build instructions are at
[AppImage/type2-runtime tag 20251108](https://github.com/AppImage/type2-runtime/tree/20251108)
and [BUILD.md](https://github.com/AppImage/type2-runtime/blob/20251108/BUILD.md).
The build tool's source and license are available at
[AppImage/appimagetool tag 1.9.1](https://github.com/AppImage/appimagetool/tree/1.9.1).
The build tool itself is not embedded in the Blog Studio AppImage.

`AppRun` forwards all arguments directly to the bundled PyInstaller executable,
without using external commands or changing the caller's working directory.
It therefore supports Unicode paths and a missing Python/Git installation.
`APPIMAGE_EXTRACT_AND_RUN=1` lets the runtime extract and run without FUSE; the
runtime still needs an executable temporary directory and enough free space.
