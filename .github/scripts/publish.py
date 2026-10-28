"""Yalnızca önceden derlenmiş, imzalı dağıtım paketlerini yayıma hazırlar."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY = "h1Sta/0Dzle4Vg/WfeqcBVONXUDvx7DtAhlrt0Uy17A="


def prepare(plan, manifest, output):
    payload = json.dumps({key: value for key, value in manifest.items() if key != 'signature'}, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY)).verify(base64.b64decode(manifest['signature']), payload)
    version = manifest['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or plan['version'] != version:
        raise ValueError('Invalid release version')
    direct = 'packages' in plan
    items = plan['packages'] if direct else plan['artifacts']
    if {item['platform'] for item in items} != set(manifest['packages']):
        raise ValueError('Missing or unexpected release platform')
    output = Path(output)
    output.mkdir(exist_ok=True)
    for item in items:
        platform = item['platform']
        if not re.fullmatch(r'(windows|linux)-(x64|arm64)', platform):
            raise ValueError('Invalid platform')
        url = urllib.parse.urlsplit(item['download_url'])
        allowed = url.hostname in {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'} if direct else bool(url.hostname and url.hostname.endswith('.oaiusercontent.com'))
        if direct and url.hostname == 'github.com' and item['download_url'] == manifest['packages'][platform]['url']:
            allowed = True
        if url.scheme != 'https' or not allowed or url.username or url.password or url.port not in (None,443):
            raise ValueError('Invalid artifact download origin')
        with urllib.request.urlopen(item['download_url'], timeout=90) as response:
            data = response.read(200_000_001)
        if len(data) > 200_000_000:
            raise ValueError('Artifact checksum mismatch')
        filename = f'sasirirsin-{platform}-standalone.zip'
        if direct:
            package = data
        else:
            if hashlib.sha256(data).hexdigest() != item['artifact_sha256']:
                raise ValueError('Artifact checksum mismatch')
            with zipfile.ZipFile(io.BytesIO(data)) as artifact:
                matches = [name for name in artifact.namelist() if name == filename]
                if len(matches) != 1:
                    raise ValueError('Release ZIP not found in artifact')
                package = artifact.read(filename)
        expected = manifest['packages'][platform]
        if len(package) != expected['size'] or hashlib.sha256(package).hexdigest() != expected['sha256']:
            raise ValueError('Signed package checksum mismatch')
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            inventory = archive.read(f'sasirirsin-{platform}/installation.json')
        if hashlib.sha256(inventory).hexdigest() != expected['installation_sha256']:
            raise ValueError('Signed installation inventory mismatch')
        (output / filename).write_bytes(package)
        print('Verified release package:', platform, len(package))
    (output / 'update-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return version


if __name__ == '__main__':
    plan = json.loads(Path('publish-plan.json').read_text(encoding='utf-8'))
    manifest = json.loads(Path('update-manifest.json').read_text(encoding='utf-8'))
    version = prepare(plan, manifest, 'out')
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('version=' + version + '\n')
