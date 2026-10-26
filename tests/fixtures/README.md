# Synthetic blog fixtures

Every document and configuration file in `blog/` was written for the test suite.
They do not contain a user's blog posts, drafts, profile, credentials, or remote
repository configuration. The image is an embedded one-pixel test PNG.

The examples cover public and hidden posts, Korean filenames, nested YAML,
scalar tags, code, tables, math, raw HTML, Liquid, and relative image paths.
The archive page deliberately maps the display label `Deep Learning` to the
category key `Deep-Learning`.

Tests copy these files into a temporary directory before opening or editing
them. No test should discover a sibling repository or require a real blog.
