class SabreError(RuntimeError):
    """Error base de integración con Sabre."""


class SabreAuthenticationError(SabreError):
    pass


class SabreAPIError(SabreError):
    def __init__(self, status_code: int, message: str, response_body: str = "") -> None:
        super().__init__(f"Sabre respondió HTTP {status_code}: {message}")
        self.status_code = status_code
        self.response_body = response_body


class SabreWriteNotSentError(SabreError):
    """A write failed before the HTTP request could be sent."""


class SabreWriteAmbiguousError(SabreError):
    """A write may have reached Sabre; blind retry is unsafe."""
