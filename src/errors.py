"""Exception hierarchy and user-facing message formatting."""

from __future__ import annotations


class DevinActionError(Exception):
    """Base class for all devin-action errors surfaced to the user."""

    def user_message(self) -> str:
        return str(self)


class MissingInputError(DevinActionError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def user_message(self) -> str:
        return f"Missing required input: {self.name}"


class InvalidInputError(DevinActionError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name}: {reason}")
        self.name = name
        self.reason = reason

    def user_message(self) -> str:
        return f"Invalid input '{self.name}': {self.reason}"


class DevinAPIError(DevinActionError):
    """Base class for Devin API failures."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class DevinAuthError(DevinAPIError):
    def __init__(self) -> None:
        super().__init__(
            "Devin API key invalid or lacks ManageOrgSessions permission.",
            status=401,
        )


class DevinPermissionError(DevinAPIError):
    def __init__(self, org_id: str) -> None:
        super().__init__(
            f"API key lacks required permission for org {org_id}.",
            status=403,
        )
        self.org_id = org_id


class DevinNotFoundError(DevinAPIError):
    def __init__(self, org_id: str) -> None:
        super().__init__(f"Org {org_id} not found.", status=404)
        self.org_id = org_id


class DevinValidationError(DevinAPIError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Invalid session payload: {detail}", status=422)
        self.detail = detail


class DevinRateLimitError(DevinAPIError):
    def __init__(self) -> None:
        super().__init__("Devin API rate limit exceeded. Retry later.", status=429)


class DevinServerError(DevinAPIError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Devin API returned {status}: {body}", status=status)


class DevinNetworkError(DevinAPIError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Failed to reach Devin API: {reason}")
        self.reason = reason
