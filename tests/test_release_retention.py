import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github/scripts"))
import keep_latest


class ReleaseRetentionTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        patched = patch.object(keep_latest, "PUBLIC_KEY", public)
        patched.start()
        self.addCleanup(patched.stop)
        self.current, self.raw = self.make_release("1.1.4", 114)
        self.client = Mock()
        self.client.latest.return_value = self.current
        self.client.manifest.return_value = self.raw
        self.client.releases.return_value = [self.current, self.old("1.1.2", 112), self.old("1.1.3", 113)]

    def old(self, version, identifier, **extra):
        return {"id": identifier, "tag_name": "v" + version, "draft": False, "prerelease": False, **extra}

    def make_release(self, version, identifier):
        packages = {}
        assets = []
        for index, platform in enumerate(sorted(keep_latest.PLATFORMS), 1):
            filename = f"sasirirsin-{platform}-standalone.zip"
            digest = str(index) * 64
            packages[platform] = {"url": f"https://github.com/{keep_latest.REPOSITORY}/releases/download/v{version}/{filename}",
                                  "size": index * 100, "sha256": digest, "installation_sha256": "a" * 64}
            assets.append({"name": filename, "state": "uploaded", "size": index * 100, "digest": "sha256:" + digest})
        manifest = {"schema": 1, "version": version, "urgent": False, "packages": packages}
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        manifest["signature"] = base64.b64encode(self.key.sign(canonical)).decode()
        raw = json.dumps(manifest).encode()
        assets.append({"name": "update-manifest.json", "state": "uploaded", "size": len(raw),
                       "digest": "sha256:" + hashlib.sha256(raw).hexdigest()})
        return {**self.old(version, identifier), "assets": assets}, raw

    def test_retains_verified_current_and_does_not_delete_a_future_draft(self):
        self.client.releases.return_value.append(self.old("1.1.5", 115, draft=True))
        self.assertEqual(keep_latest.prune(self.client, apply=True), ["v1.1.2", "v1.1.3"])
        self.assertEqual([call.args[0] for call in self.client.delete.call_args_list], ["v1.1.2", "v1.1.3"])

    def test_default_is_a_non_mutating_preview(self):
        self.assertEqual(keep_latest.prune(self.client), ["v1.1.2", "v1.1.3"])
        self.client.delete.assert_not_called()

    def test_missing_or_incomplete_platform_preserves_every_old_release(self):
        for mode in ("missing", "uploading", "wrong_digest"):
            with self.subTest(mode=mode):
                current = copy.deepcopy(self.current)
                if mode == "missing":
                    current["assets"].pop(1)
                elif mode == "uploading":
                    current["assets"][1]["state"] = "new"
                else:
                    current["assets"][1]["digest"] = "sha256:" + "0" * 64
                self.client.latest.return_value = current
                with self.assertRaises(keep_latest.RetentionError):
                    keep_latest.prune(self.client, apply=True)
                self.client.delete.assert_not_called()

    def test_invalid_signature_preserves_every_old_release(self):
        data = json.loads(self.raw)
        data["urgent"] = True
        self.client.manifest.return_value = json.dumps(data).encode()
        with self.assertRaises(keep_latest.RetentionError):
            keep_latest.prune(self.client, apply=True)
        self.client.delete.assert_not_called()

    def test_newer_published_version_prevents_cleanup_of_a_stale_latest_pointer(self):
        self.client.releases.return_value.append(self.old("1.1.5", 115))
        with self.assertRaises(keep_latest.RetentionError):
            keep_latest.prune(self.client, apply=True)
        self.client.delete.assert_not_called()

    def test_superseded_publishing_job_cannot_delete_releases(self):
        self.assertEqual(keep_latest.prune(self.client, apply=True, expected_tag="v1.1.3"), [])
        self.client.manifest.assert_not_called()
        self.client.delete.assert_not_called()

    def test_stops_before_any_deletion_if_latest_changes(self):
        newer, _ = self.make_release("1.1.5", 115)
        self.client.latest.side_effect = [self.current, newer]
        with self.assertRaises(keep_latest.RetentionError):
            keep_latest.prune(self.client, apply=True)
        self.client.delete.assert_not_called()

    def test_tag_must_match_signed_version(self):
        current = copy.deepcopy(self.current)
        current["tag_name"] = "v1.1.5"
        self.client.latest.return_value = current
        with self.assertRaises(keep_latest.RetentionError):
            keep_latest.prune(self.client, apply=True)
        self.client.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
