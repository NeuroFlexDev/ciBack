"""Normalize protected image references without granting access from their text."""
import re


_IMAGE_PATH = re.compile(r'''(?<![\w/])(?:/api)?/images/(\d+)/content(?=$|[\s?#)\]>"'])''')


def image_ids_in(value):
    """Inspect every nested content string, treating /images and /api/images alike."""
    if isinstance(value, str):
        return {int(match) for match in _IMAGE_PATH.findall(value)}
    if isinstance(value, dict):
        return set().union(*(image_ids_in(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(image_ids_in(item) for item in value))
    return set()
