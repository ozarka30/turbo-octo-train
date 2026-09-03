"""Error classes shared across the package."""


class FatalError(RuntimeError):
    """A problem that will not go away by trying the next frame: missing login,
    bad API key, no audio player. The session should stop and tell the user."""
