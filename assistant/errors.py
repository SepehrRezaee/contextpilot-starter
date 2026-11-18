class AssistantError(Exception):
    """Public, content-free error codes; never forward raw upstream exceptions."""

    def __init__(self, code: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(code)

