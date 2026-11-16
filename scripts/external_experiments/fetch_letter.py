"""Fetch the small official UCI archive and preserve source/license bindings."""
from pathlib import Path
import argparse
import datetime
import io
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.external_experiments.common import sha, write

PAGE = 'https://archive.ics.uci.edu/dataset/59/letter+recognition'
DOWNLOAD = 'https://archive.ics.uci.edu/static/public/59/letter+recognition.zip'
EXPECTED_RAW_SHA = '2b89f3602cf768d3c8355267d2f13f2417809e101fc2b5ceee10db19a60de6e2'


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'data-asset-research-source-reader'})
    with urllib.request.urlopen(request, timeout=30) as response:
        blob = response.read(limit + 1)
    if len(blob) > limit:
        raise ValueError('SOURCE_DOWNLOAD_LIMIT_EXCEEDED')
    return blob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.is_relative_to(ROOT):
        raise ValueError('DATA_MUST_BE_OUTSIDE_SOURCE_CHECKOUT')
    out.mkdir(parents=True, exist_ok=False)
    page = fetch(PAGE, 5 * 1024 * 1024)
    (out / 'uci_page.html').write_bytes(page)
    if 'CC BY 4.0' not in page.decode('utf8'):
        raise ValueError('OFFICIAL_LICENSE_TEXT_NOT_FOUND')
    blob = fetch(DOWNLOAD, 5 * 1024 * 1024)
    archive = out / 'letter_recognition_original.zip'
    archive.write_bytes(blob)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        # Read only the two fixed filenames; never extract arbitrary members.
        for name in ['letter-recognition.data', 'letter-recognition.names']:
            info = z.getinfo(name)
            if info.file_size > 2 * 1024 * 1024:
                raise ValueError('UNEXPECTED_MEMBER_SIZE')
            (out / name).write_bytes(z.read(info))
    raw = out / 'letter-recognition.data'
    if sha(raw) != EXPECTED_RAW_SHA:
        raise ValueError('SOURCE_VERSION_CHANGED_REQUIRES_REVIEW')
    write(out / 'source.json', {
        'dataset': 'Letter Recognition', 'source_url': PAGE, 'download_url': DOWNLOAD,
        'license': 'CC BY 4.0', 'license_url': PAGE,
        'attribution': 'Frey, P. & Slate, D. (1991). Letter Recognition. UCI Machine Learning Repository. DOI: 10.24432/C5ZP40.',
        'retrieved_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'raw_sha256': sha(raw), 'raw_size_bytes': raw.stat().st_size,
        'archive_sha256': sha(archive), 'archive_size_bytes': len(blob),
        'source_page_sha256': sha(out / 'uci_page.html'),
    })
    print('Source downloaded and hash/official license recorded; no model training started.')


if __name__ == '__main__':
    main()
