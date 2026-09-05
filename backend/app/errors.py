class LLMError(Exception):
    """Base error for failures at the text LLM boundary."""

    code = "LLM_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        self.code = code or self.code
        self.message = message
        super().__init__(f"{self.code}: {message}")


class LLMNotConfiguredError(LLMError):
    code = "LLM_NOT_CONFIGURED"


class LLMRequestError(LLMError):
    code = "LLM_REQUEST_FAILED"


class LLMInvalidResponseError(LLMError):
    code = "LLM_INVALID_RESPONSE"


# Short aliases keep the exception names convenient for API/task boundaries.
LLMNotConfigured = LLMNotConfiguredError
LLMInvalidResponse = LLMInvalidResponseError
