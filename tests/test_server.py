"""Independent regression tests for data preservation and publication boundaries.

Every test uses a throwaway repository. No test writes to the real blog or pushes
anything to a network remote.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "server.py"
FIXTURE_BLOG = Path(__file__).resolve().parent / "fixtures" / "blog"
spec = importlib.util.spec_from_file_location("blog_studio_server", MODULE_PATH)
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)

POST_PATH = "_posts/ai/llm/2026-03-17-lora.md"
POST = '''---
title: "LoRA: 원문 보존"
excerpt: "요약"
categories:
  - AI
  - LLM
tags: [LoRA, PEFT]
feature: true
toc: true
published: false
last_modified_at: 2026-03-17
custom:
  nested: [one, two]
  enabled: true
---

# 수식과 코드

$$W = W_0 + BA$$

```python
print("<script>this is code</script>")
```

![상대 이미지](../../../assets/images/existing.png)

<div class="notice">원본 HTML</div>

{% include notice_box.html %}
'''
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blog-studio-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "blog"
        self.repo.mkdir()
        # Windows temp paths may use an 8.3 alias (RUNNER~1); Studio uses the
        # resolved long path. Keep fixture paths in the same canonical form.
        self.repo = self.repo.resolve()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Studio Test")
        self.git("config", "user.email", "studio-test@example.invalid")
        self.write("_config.yml", "url: https://example.invalid\ntimezone: Asia/Seoul\npermalink: /:categories/:title/\n")
        self.write(".gitignore", ".blog-studio/\n")
        self.write("_data/categories.yml", "categories:\n  - lv1_category: AI\n    lv2:\n      - lv2_category: LLM\n        url: /category/ai/llm/\n")
        self.write(POST_PATH, POST)
        self.write("assets/images/existing.png", PNG)
        self.write("unrelated.txt", "original\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial fixture")
        self.studio = server.Studio(self.repo)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, text=True, encoding="utf-8", capture_output=True).stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_bytes(content.encode("utf-8"))
        return path

    def open(self):
        return self.studio.open_post(POST_PATH)

    def update(self, draft, **fields):
        return self.studio.save_draft(dict(draft, **fields))

    def publish(self, draft, mode="save"):
        return self.studio.publish({"id": draft["id"], "version": draft["version"], "mode": mode})

    def assert_studio_error(self, call, status=None):
        with self.assertRaises(server.StudioError) as result:
            call()
        if status is not None:
            self.assertEqual(result.exception.status, status)
        return result.exception


class PreservationTests(RepositoryCase):
    def test_open_save_and_export_preserve_exact_original(self):
        d = self.open()
        self.assertFalse(d["published"])
        self.assertEqual(d["category"], "ai/llm")
        d = self.update(d)
        self.assertEqual(self.studio.export(d["id"]), POST)
        self.publish(d)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_metadata_edit_retains_unmanaged_yaml_and_body(self):
        d = self.update(self.open(), title="변경한 제목: 테스트", excerpt="새 요약")
        result = self.publish(d)
        rendered = (self.repo / POST_PATH).read_text(encoding="utf-8")
        before, _, body_before = server.split_post(POST)
        after, _, body_after = server.split_post(rendered)
        for key in ("custom", "last_modified_at", "categories", "published", "feature"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(body_after, body_before)
        self.assertEqual(result["path"], POST_PATH)
        self.assertEqual(after["title"], "변경한 제목: 테스트")

    def test_original_path_is_stable_across_title_date_category_edits(self):
        d = self.open()
        original = {key: d[key] for key in ("date", "slug", "category")}
        d = self.update(d, title="새 제목", slug="changed", date="2025-01-01", category="other")
        self.assertEqual({key: d[key] for key in original}, original)
        self.assertEqual(self.publish(d)["path"], POST_PATH)

    def test_draft_save_does_not_touch_post_or_git(self):
        head = self.git("rev-parse", "HEAD")
        d = self.update(self.open(), body="아직 공개하면 안 되는 내용")
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.studio.get_draft(d["id"])["body"], "아직 공개하면 안 되는 내용")

    def test_stale_autosave_cannot_overwrite_newer_text(self):
        d = self.open()
        current = self.update(d, body="최신 작성 내용")
        self.assert_studio_error(lambda: self.update(d, body="오래된 작성 내용"), 409)
        self.assertEqual(self.studio.get_draft(d["id"])["body"], current["body"])

    def test_review_version_must_still_be_current(self):
        d = self.open()
        self.update(d, body="검토 이후 변경")
        self.assert_studio_error(lambda: self.publish(d), 409)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)

    def test_external_source_change_cannot_be_overwritten(self):
        d = self.update(self.open(), body="편집기 변경")
        external = POST + "\n외부 편집기 변경\n"
        self.write(POST_PATH, external)
        self.assertTrue(self.studio.check(d["id"])["errors"])
        self.assert_studio_error(lambda: self.publish(d), 409)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), external)

    def test_restore_keeps_new_source_revision_after_publish(self):
        d = self.open()
        initial = d["version"]
        changed = self.update(d, body="새 본문")
        published = self.publish(changed)["draft"]
        restored = self.studio.restore({"id": d["id"], "version": published["version"], "history_id": f"{initial:010}"})
        self.assertEqual(restored["body"], d["body"])
        self.assertEqual(restored["source_revision"], published["source_revision"])
        self.assertFalse(self.studio.check(restored["id"])["errors"])
        self.assertGreater(len(self.studio.history(d["id"])["items"]), 2)

    def test_delete_draft_preserves_post_and_recovery_history(self):
        d = self.open()
        self.studio.delete_draft(d["id"])
        self.assert_studio_error(lambda: self.studio.get_draft(d["id"]), 404)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)
        self.assertTrue((self.repo / ".blog-studio/history" / d["id"]).is_dir())

    def test_new_draft_collision_and_future_date_are_blocked(self):
        d = self.studio.save_draft({"title": "충돌", "body": "내용", "date": "2026-03-17", "slug": "lora", "category": "ai/llm"})
        self.assertTrue(self.studio.check(d["id"])["errors"])
        self.assert_studio_error(lambda: self.publish(d), 409)
        future = self.update(d, date="2999-01-01", slug="new")
        self.assertTrue(self.studio.check(future["id"])["errors"])

    def test_import_preserves_nested_yaml_and_unicode(self):
        d = self.studio.import_markdown({"name": "2026-01-01-가져온-글.md", "text": POST})
        self.assertEqual(d["title"], "LoRA: 원문 보존")
        self.assertFalse(d["published"])
        self.assertEqual(d["category"], "ai/llm")
        self.assertEqual(server.split_post(self.studio.export(d["id"]))[0]["custom"], {"nested": ["one", "two"], "enabled": True})
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)

    def test_existing_relative_image_is_not_reported_missing(self):
        report = self.studio.check(self.open()["id"])
        self.assertFalse(any("찾을 수 없는 이미지" in w for w in report["warnings"]))

    def test_crlf_and_missing_final_newline_survive_no_op_export(self):
        source = POST.rstrip("\n").replace("\n", "\r\n")
        self.write(POST_PATH, source.encode())
        d = self.open()
        self.assertEqual(self.studio.export(d["id"]), source)

    def test_new_category_uses_archive_key_instead_of_display_label(self):
        self.write("_data/categories.yml", "categories:\n  - lv1_category: AI\n    lv2:\n      - lv2_category: Deep Learning\n        url: /category/ai/deep_learning/\n")
        self.write("_pages/categories/category_ai_deeplearning.html", "---\nlayout: category_archive\npermalink: /category/ai/deep_learning/\n---\n{% assign posts = site.categories['Deep-Learning'] %}\n")
        studio = server.Studio(self.repo)
        d = studio.save_draft({"title": "새 딥러닝 글", "body": "내용", "date": "2026-01-01", "category": "ai/deep_learning"})
        meta = server.split_post(studio.export(d["id"]))[0]
        self.assertEqual(meta["categories"], ["AI", "Deep-Learning"])
        self.assertIn("Deep Learning", studio.categories[0]["label"])


class ImagesAndBoundariesTests(RepositoryCase):
    def upload(self, d, name="한글 그림.png", data=PNG):
        return self.studio.upload({"draft_id": d["id"], "name": name, "data": base64.b64encode(data).decode()})

    def test_images_are_private_until_publish_and_only_references_copy(self):
        d = self.open()
        used = self.upload(d)
        unused = self.upload(d, "unused.png", PNG + b"unused")
        self.assertFalse((self.repo / "assets/images/posts").exists())
        d = self.update(d, body=d["body"] + f'\n![이미지]({used["url"]})\n')
        result = self.publish(d)
        from urllib.parse import unquote
        self.assertEqual((self.repo / unquote(used["url"]).lstrip("/")).read_bytes(), PNG)
        self.assertFalse((self.repo / unquote(unused["url"]).lstrip("/")).exists())
        self.assertFalse(result["pushed"])

    def test_same_name_different_images_do_not_overwrite(self):
        d = self.open()
        first = self.upload(d, "image.png")
        second = self.upload(d, "image.png", PNG + b"another-image")
        self.assertNotEqual(first["url"], second["url"])
        self.assertEqual(first["url"], self.upload(d, "image.png")["url"])

    def test_invalid_and_active_uploads_are_rejected(self):
        d = self.open()
        for name, data in (("evil.svg", b'<svg onload="alert(1)"></svg>'), ("fake.png", b"not a PNG"), ("evil.html", PNG), ("empty.png", b"")):
            with self.subTest(name=name):
                self.assert_studio_error(lambda: self.upload(d, name, data))
        self.assert_studio_error(lambda: self.studio.upload({"draft_id": d["id"], "name": "x.png", "data": "!!!"}))

    def test_traversal_and_invalid_draft_ids_are_rejected(self):
        for path in ("../secret.md", "_posts/../../secret.md", "/etc/passwd", "_posts/ai/llm/../../../../secret.md", "_posts\\outside.md"):
            with self.subTest(path=path):
                self.assert_studio_error(lambda: self.studio.open_post(path))
        for value in ("../outside", "../../", None, "x" * 32):
            self.assert_studio_error(lambda: self.studio.get_draft(value))

    def test_symlink_source_and_upload_parent_are_rejected(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_bytes(POST.encode("utf-8"))
        (self.repo / "_posts/linked.md").symlink_to(outside)
        self.assert_studio_error(lambda: self.studio.open_post("_posts/linked.md"), 403)
        d = self.open()
        (self.repo / ".blog-studio/uploads" / d["id"]).symlink_to(Path(self.temp.name), target_is_directory=True)
        self.assert_studio_error(lambda: self.upload(d), 403)

    def test_client_cannot_replace_internal_source_path_or_baseline(self):
        d = self.open()
        changed = self.update(d, source_path="unrelated.txt", source_revision="invalid", _header="injected", _baseline={})
        self.assertEqual(changed["source_path"], POST_PATH)
        self.assertEqual(changed["source_revision"], d["source_revision"])
        self.assertEqual(changed["_header"], d["_header"])


class GitPublicationTests(RepositoryCase):
    def test_commit_contains_only_this_post_and_leaves_unrelated_work(self):
        self.write("unrelated.txt", "unrelated local changes\n")
        self.write("untracked.txt", "untracked local changes\n")
        d = self.update(self.open(), title="선택한 글만 커밋")
        result = self.publish(d, "commit")
        self.assertTrue(result["commit"])
        self.assertFalse(result["pushed"])
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), POST_PATH)
        self.assertEqual((self.repo / "unrelated.txt").read_text(encoding="utf-8"), "unrelated local changes\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertIn("unrelated.txt", self.git("status", "--porcelain"))

    def test_existing_staged_work_blocks_commit_without_mutation(self):
        d = self.update(self.open(), body="커밋 후보")
        self.write("unrelated.txt", "already staged\n")
        self.git("add", "unrelated.txt")
        head = self.git("rev-parse", "HEAD")
        index = self.git("diff", "--cached")
        self.assert_studio_error(lambda: self.publish(d, "commit"), 409)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("diff", "--cached"), index)

    def test_file_save_preserves_existing_staged_work(self):
        self.write("unrelated.txt", "already staged\n")
        self.git("add", "unrelated.txt")
        staged = self.git("diff", "--cached")
        d = self.update(self.open(), body="파일만 저장")
        self.publish(d, "save")
        self.assertEqual(self.git("diff", "--cached"), staged)
        self.assertIn("파일만 저장", (self.repo / POST_PATH).read_text(encoding="utf-8"))

    def test_commit_failure_rolls_back_post_and_retains_draft(self):
        d = self.update(self.open(), body="보존할 초안")
        with patch.object(self.studio, "_commit", side_effect=server.StudioError("Simulated git failure", 409)):
            self.assert_studio_error(lambda: self.publish(d, "commit"), 409)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)
        self.assertEqual(self.studio.get_draft(d["id"])["body"], "보존할 초안")

    def test_staging_after_review_is_rechecked_under_index_lock(self):
        d = self.update(self.open(), body="검토 뒤 스테이징 경합")
        original_commit = self.studio._commit
        def stage_then_commit(files, title):
            self.write("unrelated.txt", "concurrent staged changes\n")
            self.git("add", "unrelated.txt")
            return original_commit(files, title)
        head = self.git("rev-parse", "HEAD")
        with patch.object(self.studio, "_commit", side_effect=stage_then_commit):
            self.assert_studio_error(lambda: self.publish(d, "commit"), 409)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "unrelated.txt")
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)

    def test_existing_git_lock_is_not_removed_or_ignored(self):
        d = self.update(self.open(), body="동시 Git 작업")
        lock = self.repo / ".git/index.lock"
        lock.write_text("another process owns this lock", encoding="utf-8")
        self.assert_studio_error(lambda: self.publish(d, "commit"), 409)
        self.assertEqual(lock.read_text(encoding="utf-8"), "another process owns this lock")
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)

    def test_index_install_failure_preserves_already_committed_content(self):
        d = self.update(self.open(), body="커밋 완료 후 인덱스 실패")
        replace = os.replace
        index_path = (self.repo / ".git/index").resolve()
        def fail_index_replace(source, target):
            if Path(target).resolve() == index_path:
                raise OSError("Simulated index replacement failure")
            return replace(source, target)
        head = self.git("rev-parse", "HEAD")
        with patch.object(server.os, "replace", side_effect=fail_index_replace):
            self.assert_studio_error(lambda: self.publish(d, "commit"), 500)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), head)
        committed = self.git("show", "HEAD:" + POST_PATH)
        self.assertIn("커밋 완료 후 인덱스 실패", committed)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8").strip(), committed)

    def setup_remote(self):
        self.remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], check=True, capture_output=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")

    def test_push_to_local_bare_remote_sends_only_intended_commit(self):
        self.setup_remote()
        d = self.update(self.open(), body="전송할 내용")
        result = self.publish(d, "push")
        self.assertTrue(result["pushed"])
        self.assertIsNone(result["push_error"])
        head = self.git("rev-parse", "HEAD")
        remote_head = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "main"], check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        self.assertEqual(head, remote_head)

    def test_unrelated_unpushed_commit_blocks_push(self):
        self.setup_remote()
        self.write("unrelated.txt", "unrelated commit\n")
        self.git("add", "unrelated.txt")
        self.git("commit", "-m", "Unrelated unpublished work")
        d = self.update(self.open(), body="블로그 초안")
        self.assert_studio_error(lambda: self.publish(d, "push"), 409)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)

    def test_rejected_push_keeps_local_commit_and_retry_succeeds(self):
        self.setup_remote()
        hook = self.remote / "hooks/pre-receive"
        hook.write_bytes(b"#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        d = self.update(self.open(), body="실패해도 남을 내용")
        result = self.publish(d, "push")
        self.assertFalse(result["pushed"])
        self.assertTrue(result["push_error"])
        self.assertEqual(self.git("rev-parse", "HEAD"), result["commit"])
        self.assertIn("실패해도 남을 내용", (self.repo / POST_PATH).read_text(encoding="utf-8"))
        hook.unlink()
        retry = self.publish(result["draft"], "push")
        self.assertTrue(retry["pushed"])
        self.assertEqual(retry["commit"], result["commit"])

    def test_multiple_commits_from_same_draft_can_be_pushed_together(self):
        self.setup_remote()
        d = self.update(self.open(), body="첫 번째 로컬 기록")
        first = self.publish(d, "commit")
        d = self.update(first["draft"], body="두 번째 로컬 기록")
        second = self.publish(d, "commit")
        d = self.update(second["draft"], body="전송 직전 최종 기록")
        result = self.publish(d, "push")
        self.assertTrue(result["pushed"])
        self.assertEqual(self.git("rev-list", "--count", "origin/main..HEAD"), "0")

    def test_other_commit_among_owned_commits_still_blocks_push(self):
        self.setup_remote()
        first = self.publish(self.update(self.open(), body="이 글의 커밋"), "commit")
        self.write("unrelated.txt", "foreign unpublished commit\n")
        self.git("add", "unrelated.txt")
        self.git("commit", "-m", "Foreign commit")
        second = self.publish(self.update(first["draft"], body="이 글의 두 번째 커밋"), "commit")
        self.assert_studio_error(lambda: self.publish(second["draft"], "push"), 409)

    def test_remote_ahead_is_detected_before_changing_post(self):
        self.setup_remote()
        other = Path(self.temp.name) / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)], check=True, capture_output=True)
        def other_git(*args):
            subprocess.run(["git", "-C", str(other), *args], check=True, capture_output=True)
        other_git("config", "user.name", "Other writer")
        other_git("config", "user.email", "other@example.invalid")
        (other / "remote-only.txt").write_text("remote work", encoding="utf-8")
        other_git("add", "remote-only.txt")
        other_git("commit", "-m", "Remote change")
        other_git("push", "origin", "main")
        d = self.update(self.open(), body="동기화 전 초안")
        self.assert_studio_error(lambda: self.publish(d, "push"), 409)
        self.assertEqual((self.repo / POST_PATH).read_text(encoding="utf-8"), POST)


class HTTPBoundaryTests(RepositoryCase):
    def setUp(self):
        super().setUp()
        self.http = server.create_server(self.repo, port=0)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_http)
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def close_http(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

    def request(self, path, data=None, headers=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(self.base + path, data=body, headers=headers or {}, method=method)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), dict(response.headers)

    def post_headers(self):
        return {"Content-Type": "application/json", "X-Studio-Token": self.http.token, "Origin": self.base}

    def test_bootstrap_and_authorized_draft_save(self):
        status, body, headers = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["token"], self.http.token)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, body, _ = self.request("/api/drafts", {"title": "HTTP 초안"}, self.post_headers())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["title"], "HTTP 초안")

    def test_missing_token_cross_origin_and_wrong_host_rejected(self):
        invalid = [
            {"Content-Type": "application/json"},
            dict(self.post_headers(), Origin="https://attacker.invalid"),
            dict(self.post_headers(), Host="attacker.invalid"),
            dict(self.post_headers(), **{"Sec-Fetch-Site": "cross-site"}),
        ]
        for headers in invalid:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request("/api/drafts", {"title": "forbidden"}, headers)[0], 403)
        self.assertEqual(self.request("/api/bootstrap", headers={"Host": "attacker.invalid"})[0], 403)

    def test_content_type_and_non_object_json_rejected(self):
        headers = self.post_headers()
        headers["Content-Type"] = "text/plain"
        self.assertEqual(self.request("/api/drafts", {}, headers)[0], 415)
        self.assertEqual(self.request("/api/drafts", [1, 2], self.post_headers())[0], 400)

    def test_private_files_and_traversal_not_served(self):
        d = self.http.studio.open_post(POST_PATH)
        for path in (f'/.blog-studio/drafts/{d["id"]}.json', "/.git/config", "/assets/../../.git/config", "/assets/%2e%2e/%2e%2e/.git/config", "/assets/not-a-picture.txt"):
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], (403, 404))


class FixtureBlogIntegrationTests(unittest.TestCase):
    def test_every_fixture_post_survives_open_save_export_byte_for_byte(self):
        with tempfile.TemporaryDirectory(prefix="blog-studio-roundtrip-") as name:
            repo = Path(name).resolve()
            shutil.copytree(FIXTURE_BLOG, repo, dirs_exist_ok=True)
            studio = server.Studio(repo)
            posts = list((repo / "_posts").rglob("*.md"))
            self.assertGreaterEqual(len(posts), 4)
            for path in posts:
                relative = path.relative_to(repo).as_posix()
                with self.subTest(post=relative):
                    original = path.read_bytes()
                    d = studio.open_post(relative)
                    saved = studio.save_draft(d)
                    self.assertEqual(studio.export(saved["id"]).encode("utf-8"), original)
                    self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
