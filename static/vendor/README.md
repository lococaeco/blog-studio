# Browser dependencies

These files are checked in so that Blog Studio's editor, Korean UI, math renderer,
and fonts work without loading a third-party CDN at runtime.

| Component | Version | Source |
| --- | --- | --- |
| TOAST UI Editor browser bundle | 3.2.2 | [Official NHN distribution](https://uicdn.toast.com/editor/3.2.2/toastui-editor-all.min.js) |
| TOAST UI Editor CSS and Korean translation | 3.2.2 | [Official npm package](https://registry.npmjs.org/@toast-ui/editor/-/editor-3.2.2.tgz) |
| KaTeX, auto-render, and fonts | 0.16.11 | [Official npm package](https://registry.npmjs.org/katex/-/katex-0.16.11.tgz) |
| DOMPurify | 3.4.16 | [Official npm package](https://registry.npmjs.org/dompurify/-/dompurify-3.4.16.tgz) |

The editor's npm JavaScript entry point externalizes ProseMirror. The official
`all.min.js` bundle is used here because it includes the browser dependencies.
The CSS includes its icons as data URLs and needs no separate icon downloads.
The full KaTeX font directory is included for all formats referenced by its CSS.

`manifest.json` records each downloaded file's upstream URL, size, and SHA-256.
The integrity hashes provided by the npm registry were checked before extracting
the package files. Vendor files are unmodified upstream distributions.

License texts are in `licenses/`. They cover TOAST UI Editor, ToastMark and its
CommonMark-derived parser, ProseMirror and related utilities, DOMPurify, KaTeX,
and the other runtime libraries represented in the editor's upstream lockfile.
The editor bundle also retains its upstream Microsoft helper-code notice.

The editor still contains its original DOMPurify 2.3.3 dependency. The application
loads the separate DOMPurify distribution and uses it for its HTML sanitizer.
TOAST UI Editor's `usageStatistics` option is disabled by the application.

When updating a dependency, update its licenses and manifest together, verify
browser startup and image insertion, and recheck existing Markdown and math
round trips before replacing the pinned version.
