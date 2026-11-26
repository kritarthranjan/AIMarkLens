"""User-facing error type shared by engines and the UI."""


class LabError(Exception):
    """An expected failure with a plain-language explanation and an optional hint."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


def describe_exception(exc: BaseException) -> tuple[str, str]:
    """Turn any exception into (message, hint) suitable for an error banner."""
    if isinstance(exc, LabError):
        return exc.message, exc.hint
    if isinstance(exc, MemoryError):
        return ("There was not enough memory to finish this task.",
                "Close other programs or lower the forensic crop size in Settings, then retry.")
    if isinstance(exc, PermissionError):
        return (f"Access was denied: {exc.filename or exc}",
                "Check that the file is not locked by another program and that you can read it.")
    if isinstance(exc, FileNotFoundError):
        return (f"The file could not be found: {exc.filename or exc}",
                "It may have been moved or deleted. Choose the file again.")
    if isinstance(exc, OSError):
        return (f"A file system error occurred: {exc.strerror or exc}",
                "Check the file location and free disk space, then retry.")
    name = type(exc).__name__
    if name in ("UnidentifiedImageError", "DecompressionBombError"):
        return ("The image could not be decoded safely.",
                f"{exc}. The file may be damaged, or larger than the safe decoding limit.")
    return (f"An unexpected error occurred ({name}).",
            "Open the details for the technical trace. Your original file has not been modified.")
