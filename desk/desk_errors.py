
"""Ek hi tarah ki error jo user ko saaf Hindi/Hinglish message ke saath dikhti hai."""


class DeskError(Exception):
    """code = chhota naam (UI isse pehchanta hai), message = user ke liye sentence, status = HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
