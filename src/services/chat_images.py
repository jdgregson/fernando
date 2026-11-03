import base64
import hashlib
import os
import re
import tempfile
from pathlib import Path


CACHE_ROOT = Path(__file__).resolve().parents[2] / 'data' / 'image_cache'
IMAGE_TYPES = {'png': 'image/png', 'jpeg': 'image/jpeg', 'jpg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp', 'svg': 'image/svg+xml'}


def cache_image(session_id, data, mime):
    if not re.fullmatch(r'[a-f0-9]{8}', session_id):
        raise ValueError('Invalid image session ID')
    extension = next(ext for ext, value in IMAGE_TYPES.items() if value == mime)
    folder = CACHE_ROOT / session_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (hashlib.sha256(data).hexdigest() + '.' + extension)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
            stream.write(data)
        os.replace(stream.name, path)
    return str(path)


def project_images(session_id, value):
    if isinstance(value, list):
        return [project_images(session_id, item) for item in value]
    if not isinstance(value, dict):
        return value
    image = value.get('Image')
    if isinstance(image, dict) and image.get('format') in IMAGE_TYPES:
        source = image.get('source', {})
        if source.get('kind') == 'bytes' and isinstance(source.get('data'), list):
            mime = IMAGE_TYPES[image['format']]
            path = cache_image(session_id, bytes(source['data']), mime)
            return {**value, 'Image': {'type': 'image', 'mimeType': mime, 'path': path}}
    if value.get('type') == 'image' and value.get('mimeType') in IMAGE_TYPES.values() and isinstance(value.get('data'), str):
        path = cache_image(session_id, base64.b64decode(value['data'], validate=True), value['mimeType'])
        return {**{key: item for key, item in value.items() if key != 'data'}, 'path': path}
    return {key: project_images(session_id, item) for key, item in value.items()}
