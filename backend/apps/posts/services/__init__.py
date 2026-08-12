"""Post services package.

Re-export the post write operations so callers can use ``services.create_post``
etc. Media operations live in ``services.media``.
"""
from .posts import create_post, delete_post

__all__ = ["create_post", "delete_post"]
