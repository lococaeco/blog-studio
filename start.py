#!/usr/bin/env python3
"""Create an isolated Python environment, then start the local blog editor."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import venv


TOOL_DIR = Path(__file__).resolve().parent
ENV_DIR = TOOL_DIR / ".venv"
REQUIREMENTS = TOOL_DIR / "requirements.txt"


def environment_python() -> Path:
    return ENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_environment() -> Path:
    python = environment_python()
    if not python.is_file():
        print("Blog Studio: 전용 Python 환경을 준비합니다.", flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(ENV_DIR)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "Python 가상환경을 만들 수 없습니다. Python 3.10 이상과 venv를 확인하세요.\n"
                "Ubuntu/Debian: sudo apt install python3-venv\n"
                f"자세한 내용: {exc}"
            ) from exc
    dependency_check = subprocess.run(
        [str(python), "-c", "import yaml; assert yaml.__version__ == '6.0.3'"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if dependency_check.returncode:
        print("Blog Studio: 첫 실행에 필요한 PyYAML을 설치합니다. 인터넷 연결이 필요합니다.", flush=True)
        try:
            subprocess.run(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "PyYAML 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요.\n"
                f"직접 설치: \"{python}\" -m pip install -r \"{REQUIREMENTS}\""
            ) from exc
    return python


def main() -> int:
    if sys.version_info < (3, 10):
        print("Blog Studio에는 Python 3.10 이상이 필요합니다.", file=sys.stderr)
        return 1
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "사용법: python start.py [옵션]\n\n"
            "기본 실행은 데스크톱 창에서 편집할 블로그 폴더를 선택합니다.\n\n"
            "  --port PORT       사용할 포트 (기본 4310 또는 이전 사용 포트)\n"
            "  --repo PATH       편집할 실제 Jekyll 블로그 폴더\n"
            "  --headless        데스크톱 창 없이 실행\n"
            "  --no-browser      브라우저를 자동으로 열지 않기\n"
            "  --setup-only      프로그램 폴더의 .venv만 준비하기\n"
            "  --ready-file PATH 실행 주소와 블로그 경로를 JSON으로 기록\n"
            "  --exit-after SEC  지정한 초 뒤 정상 종료 (자동화용)\n"
            "  --check-gui       데스크톱 창 실행 가능 여부만 확인\n\n"
            "첫 실행에는 PyYAML 설치를 위한 인터넷 연결이 필요합니다.\n"
            "이후 글 작성과 미리보기는 인터넷 없이도 사용할 수 있습니다."
        )
        return 0
    try:
        python = ensure_environment()
        arguments = [arg for arg in sys.argv[1:] if arg != "--setup-only"]
        if "--setup-only" in sys.argv[1:]:
            print(f"Blog Studio 준비 완료: {python}")
            return 0
        command = [str(python), str(TOOL_DIR / "desktop.py"), *arguments]
        # Keep the caller's working directory so a relative --repo refers to
        # their blog, independently of where the application is installed.
        return subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError) as exc:
        print(f"Blog Studio 실행 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
