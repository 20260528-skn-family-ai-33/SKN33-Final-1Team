"""Decode actual static image bytes; preserve original and normalize display once."""
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
from ..security.errors import AuthError

MAX_IMAGE = 20 * 1024 * 1024
CONVERSION = 'cc-display-rgb-white-exif-v1'


def normalize(raw):
    if not raw or len(raw) > MAX_IMAGE: raise AuthError('PAYLOAD_TOO_LARGE', 413)
    try:
        with Image.open(BytesIO(raw)) as image:
            actual = image.format
            if actual not in ('PNG', 'JPEG', 'WEBP') or getattr(image, 'n_frames', 1) != 1:
                raise AuthError('INVALID_IMAGE', 422)
            width, height = image.size
            if max(width, height) > 8192 or width * height > 10000000: raise AuthError('IMAGE_LIMIT_EXCEEDED', 422)
            image.load()
            orientation = image.getexif().get(274, 1)
            display = ImageOps.exif_transpose(image).convert('RGBA')
            white = Image.new('RGBA', display.size, 'white')
            white.alpha_composite(display)
            output = BytesIO()
            white.convert('RGB').save(output, format='PNG', compress_level=6, optimize=False)
            data = output.getvalue()
            if len(data) > 40 * 1024 * 1024: raise AuthError('IMAGE_LIMIT_EXCEEDED', 422)
            return data, dict(actual_format=actual, original_width=width, original_height=height,
                              display_width=display.width, display_height=display.height,
                              exif_orientation=orientation, conversion_version=CONVERSION)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise AuthError('INVALID_IMAGE', 422) from None
