"""Keep one verified public distribution; never delete an incomplete successor."""
import argparse
import base64
import hashlib
import json
import re
import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from publish import PUBLIC_KEY

REPOSITORY = "nurshia/sarisinsin"
PLATFORMS = {"linux-x64", "windows-x64"}


class RetentionError(RuntimeError):
    pass


def version(tag):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    return tuple(map(int, match.groups())) if match else None


def verify_release(release, raw):
    if release.get("draft") or release.get("prerelease") or not version(release["tag_name"]):
        raise RetentionError("A published stable application release is required.")
    if len(raw) > 100_000:
        raise RetentionError("Update manifest exceeds the size limit.")
    manifest = json.loads(raw)
    payload = json.dumps({key: value for key, value in manifest.items() if key != "signature"},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY)).verify(
            base64.b64decode(manifest["signature"], validate=True), payload)
    except Exception as error:
        raise RetentionError("Update signature could not be verified.") from error
    if (manifest.get("schema") != 1 or "v" + manifest.get("version", "") != release["tag_name"]
            or set(manifest.get("packages", {})) != PLATFORMS):
        raise RetentionError("Both signed platform packages must match the current release.")
    assets = {asset["name"]: asset for asset in release["assets"]}
    if len(assets) != len(release["assets"]):
        raise RetentionError("Duplicate release assets.")
    expected = {"update-manifest.json": {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}}
    for platform, package in manifest["packages"].items():
        filename = f"sasirirsin-{platform}-standalone.zip"
        if package["url"] != f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/{filename}":
            raise RetentionError("The signed package URL does not match this release.")
        expected[filename] = package
    for name, package in expected.items():
        asset = assets.get(name, {})
        if (asset.get("state") != "uploaded" or asset.get("size") != package["size"]
                or asset.get("digest") != "sha256:" + package["sha256"]):
            raise RetentionError("A required asset is missing, incomplete or has a different checksum: " + name)


class GitHub:
    def api(self, path, *, paginate=False):
        command = ["gh", "api", "repos/" + REPOSITORY + "/" + path]
        if paginate:
            command += ["--paginate", "--slurp"]
        data = json.loads(subprocess.run(command, check=True, capture_output=True).stdout)
        return [item for page in data for item in page] if paginate else data

    def latest(self):
        return self.api("releases/latest")

    def releases(self):
        return self.api("releases?per_page=100", paginate=True)

    def manifest(self, tag):
        return subprocess.run(["gh", "release", "download", tag, "--repo", REPOSITORY,
                               "--pattern", "update-manifest.json", "--output", "-"],
                              check=True, capture_output=True).stdout

    def delete(self, tag):
        subprocess.run(["gh", "release", "delete", tag, "--repo", REPOSITORY,
                        "--yes", "--cleanup-tag"], check=True, capture_output=True)


def prune(client, *, apply=False, expected_tag=None):
    latest = client.latest()
    if expected_tag and latest["tag_name"] != expected_tag:
        print("Latest release has changed; this older publishing job will not remove anything.")
        return []
    verify_release(latest, client.manifest(latest["tag_name"]))
    floor = version(latest["tag_name"])
    releases = client.releases()
    if any(not item.get("draft") and not item.get("prerelease")
           and version(item["tag_name"]) and version(item["tag_name"]) > floor for item in releases):
        raise RetentionError("A newer published version exists; no releases were removed.")
    obsolete = sorted((item for item in releases if item["id"] != latest["id"]
                       and not item.get("draft") and version(item["tag_name"])
                       and version(item["tag_name"]) < floor), key=lambda item: version(item["tag_name"]))
    for item in obsolete:
        if client.latest()["id"] != latest["id"]:
            raise RetentionError("Latest release changed during cleanup; stopping.")
        if apply:
            client.delete(item["tag_name"])
        print(("Removed: " if apply else "Would remove: ") + item["tag_name"])
    print("Verified current release retained: " + latest["tag_name"])
    return [item["tag_name"] for item in obsolete]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove older releases only after verifying the latest distribution.")
    parser.add_argument("--apply", action="store_true", help="Delete the verified obsolete releases and their tags.")
    parser.add_argument("--tag", help="Skip if this publishing job is no longer the latest release.")
    args = parser.parse_args()
    prune(GitHub(), apply=args.apply, expected_tag=args.tag)
