"""Expected bounded source/input failures, distinct from programming errors."""


class SourceInputError(RuntimeError):
    """A source snapshot cannot be captured under the maintained input policy."""


class SourceValidationError(SourceInputError):
    """A supported source file is binary or invalid UTF-8."""


class SourceLimitError(SourceInputError):
    """A source file exceeds the bounded source-byte policy."""


class SourceAvailabilityError(SourceInputError):
    """A source or effective ignore file cannot be read as a safe regular file."""


class SourcePathPolicyError(SourceAvailabilityError):
    """A literal filename violates canonical path policy, independently of links."""


class SourceEnumerationError(SourceInputError):
    """Git or filesystem candidate enumeration is unavailable or exceeds bounds."""
