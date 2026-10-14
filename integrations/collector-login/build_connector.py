"""Build deployment-specific packages from public templates, never private artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True, help='Trusted public HTTPS origin')
    args = parser.parse_args()
    origin = args.origin.rstrip('/')
    u = urlsplit(origin)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password or
            u.path or u.query or u.fragment or any(c in origin for c in '\"\'`$\\\n\r ')):
        parser.error('--origin must be a plain HTTPS origin without credentials or a path')
    root = Path(__file__).resolve().parent
    dist = root / 'dist'
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='omnipanel-connector-') as temp:
        source = Path(temp)
        shutil.copyfile(root / 'pyproject.toml', source / 'pyproject.toml')
        shutil.copytree(root / 'rpa_collector_login', source / 'rpa_collector_login',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (source / 'rpa_collector_login/deployment.py').write_text(
            'DEFAULT_ORIGIN = ' + repr(origin) + '\n')
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                        '--no-build-isolation', '--wheel-dir', str(dist), str(source)], check=True)
    wheel = dist / 'omnipanel_collector_login-1.0.0-py3-none-any.whl'
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    wheel_url = origin + '/api/collector/repair/download/' + wheel.name + '#sha256=' + digest
    for folder, name in [('connector', 'omnipanel-collector-login-connector.zip'),
                         ('enterprise-skill', 'omnipanel-collector-login-skill.zip')]:
        with zipfile.ZipFile(dist / name, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((root / folder).rglob('*')):
                if path.is_file():
                    content = path.read_text().replace('__WHEEL_URL__', wheel_url).replace('__ORIGIN__', origin)
                    if path.suffix == '.json':
                        json.loads(content)
                    archive.writestr(str(path.relative_to(root / folder)), content)
    print('Built sanitized packages with wheel SHA256:', digest)


if __name__ == '__main__':
    main()
