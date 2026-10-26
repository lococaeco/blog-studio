# 출처와 이관 기록

Blog Studio는 `lococaeco/lococaeco.github.io`의 `tools/blog-studio`에서 개발한
글쓰기 도구를 2026-10-26에 독립 저장소로 분리한 프로젝트입니다.

- 원본: https://github.com/lococaeco/lococaeco.github.io/tree/ba2174373c8b53c7a3ea528027caf34bdc95bacb/tools/blog-studio
- 분리 기준 커밋: `ba2174373c8b53c7a3ea528027caf34bdc95bacb`
- 이전 개발 브랜치: `codex/blog-studio-desktop-20261026`

소스·정적 리소스·테스트·패키징 리소스를 가져왔으며 원래의 블로그 글, 개인 초안,
Git 이력, 가상환경, 빌드 결과물은 포함하지 않았습니다. 원본 파일 경로를 가정하던
실행·빌드 코드를 수정했고, 실제 글을 참조하던 검사는 직접 작성한 합성 fixture로
교체했습니다. 예시 화면의 글과 경로도 테스트용입니다.

원래 블로그 저장소의 테마 라이선스 고지는
[original-repository-license.txt](original-repository-license.txt)에 보존했습니다.
프로그램 자체의 라이선스는 루트 `LICENSE`, 포함된 라이브러리는
`static/vendor/licenses`에서 확인할 수 있습니다.
