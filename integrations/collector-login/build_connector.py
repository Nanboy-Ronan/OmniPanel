"""Build deployment-specific packages from public templates, never private artifacts.

    python integrations/collector-login/build_connector.py --origin https://data.example.org

One command writes the wheel, the WorkBuddy connector zip, the enterprise skill zip and
dist/release.json, so the version, wheel name and SHA256 are identical everywhere they
are pinned (cli.json, repair.sh, repair.ps1 and the server download whitelist, which
reads release.json). tests/test_collector_login_release.py fails if any of them drift.

Bump `version` in pyproject.toml (and rpa_collector_login/__init__.py) for any change
to the helper: clients that already have a version installed will not reinstall it.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

STAMP = (2026, 1, 1, 0, 0, 0)  # Fixed timestamps: rebuilding unchanged files gives identical zips.


def write_zip(path, entries):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def render(path, wheel_url, origin):
    """Fill a template, keeping its bytes (repair.ps1 needs its UTF-8 BOM and CRLF)."""
    content = path.read_bytes().decode('utf-8')
    content = content.replace('__WHEEL_URL__', wheel_url).replace('__ORIGIN__', origin)
    if path.suffix == '.json':
        json.loads(content)
    return content.encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--origin', required=True, help='Trusted public HTTPS origin')
    parser.add_argument('--dist', help='Output directory (default: dist/ next to this script)')
    args = parser.parse_args(argv)
    origin = args.origin.rstrip('/')
    u = urlsplit(origin)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password or
            u.path or u.query or u.fragment or any(c in origin for c in '\"\'`$\\\n\r ')):
        parser.error('--origin must be a plain HTTPS origin without credentials or a path')
    root = Path(__file__).resolve().parent
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    package_version = re.search(r'__version__ = "([^"]+)"',
                                (root / 'rpa_collector_login/__init__.py').read_text()).group(1)
    meta_version = json.loads((root / 'connector/connector-meta.json').read_text())['version']
    if not version == package_version == meta_version:
        sys.exit(f'versions differ: pyproject {version}, rpa_collector_login {package_version}, '
                 f'connector-meta.json {meta_version}')
    dist = Path(args.dist).resolve() if args.dist else root / 'dist'
    dist.mkdir(parents=True, exist_ok=True)
    for old in dist.glob('omnipanel_collector_login-*.whl'):
        old.unlink()
    with tempfile.TemporaryDirectory(prefix='omnipanel-connector-') as temp:
        source = Path(temp)
        shutil.copyfile(root / 'pyproject.toml', source / 'pyproject.toml')
        shutil.copytree(root / 'rpa_collector_login', source / 'rpa_collector_login',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (source / 'rpa_collector_login/deployment.py').write_text(
            'DEFAULT_ORIGIN = ' + repr(origin) + '\n')
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-q',
                        '--no-build-isolation', '--wheel-dir', str(dist), str(source)], check=True)
    wheel = dist / f'omnipanel_collector_login-{version}-py3-none-any.whl'
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    wheel_url = origin + '/api/collector/repair/download/' + wheel.name + '#sha256=' + digest
    connector = root / 'connector'
    write_zip(dist / 'omnipanel-collector-login-connector.zip',
              [(p.relative_to(connector).as_posix(), render(p, wheel_url, origin))
               for p in sorted(connector.rglob('*')) if p.is_file()])
    skill = root / 'enterprise-skill'
    write_zip(dist / 'omnipanel-collector-login-skill.zip',
              [('omnipanel-collector-login/' + p.relative_to(skill).as_posix(), render(p, wheel_url, origin))
               for p in sorted(skill.rglob('*')) if p.is_file()])
    (dist / 'release.json').write_text(
        json.dumps({'version': version, 'wheel': wheel.name, 'sha256': digest}, indent=2) + '\n')
    print('Built', wheel.name, 'SHA256:', digest)


if __name__ == '__main__':
    main()
