class ServiceError(Exception):
    """Raised by services; routes turn it into a JSON error response."""
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
