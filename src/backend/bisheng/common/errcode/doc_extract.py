from .base import BaseErrorCode


# Document extraction module error codes, module code: 116.
# Module 113 is taken by v2.7.0 F059 (platform sensitive word library) -- see
# features/v2.8.0/release-contract.md for the allocation table.
class DocExtractTypeUnavailableError(BaseErrorCode):
    """The requested doc type does not exist in this tenant, or is disabled.

    Covers both cases deliberately: telling them apart would let a caller probe
    which types another tenant has configured.
    """

    Code: int = 11601
    Msg: str = "The requested document type is not available"


class DocExtractTypeDuplicateError(BaseErrorCode):
    """A live (non-deleted) template already uses this doc type identifier."""

    Code: int = 11602
    Msg: str = "A template with this document type already exists"


class DocExtractTemplateInvalidError(BaseErrorCode):
    """Template definition rejected at save time: empty name / doc scope /
    field list, missing or duplicate field key, or an unsupported field type."""

    Code: int = 11603
    Msg: str = "Invalid template definition"


class DocExtractRenderRefInvalidError(BaseErrorCode):
    """The render template references a field key absent from the field schema."""

    Code: int = 11604
    Msg: str = "Render template references an undefined field"


class DocExtractNoParserError(BaseErrorCode):
    """No text layer, and the deployment has no recognition service configured.

    The response carries which config item is missing so the operator knows
    what to set up.
    """

    Code: int = 11605
    Msg: str = "No usable parser: document has no text layer and no recognition service is configured"


class DocExtractTypeUnknownError(BaseErrorCode):
    """Classification could not settle on a type: either no enabled template
    exists to classify against, or the top confidence fell below the threshold."""

    Code: int = 11606
    Msg: str = "Document type could not be determined"


class DocExtractFieldInvalidError(BaseErrorCode):
    """Model output failed field validation after retries were exhausted.

    The response carries the offending field keys so the caller can see which
    part of the extraction went wrong.
    """

    Code: int = 11607
    Msg: str = "Extracted fields do not match the template definition"


class DocExtractContentFetchError(BaseErrorCode):
    """Could not obtain content or model output: the model slot is unconfigured,
    a model call failed or timed out, or the recognition service call failed.

    One code for all three because they are the same outcome to the caller --
    this run produced nothing to extract from.
    """

    Code: int = 11608
    Msg: str = "Failed to obtain document content or model output"


class DocExtractDocUnreadableError(BaseErrorCode):
    Code: int = 11609
    Msg: str = "Document is unreadable or empty"


class DocExtractDocTooLargeError(BaseErrorCode):
    Code: int = 11610
    Msg: str = "Document exceeds the size limit"


class DocExtractTemplateMissingError(BaseErrorCode):
    """The template does not exist, or belongs to another tenant.

    Deliberately not distinguished -- see DocExtractTypeUnavailableError.
    """

    Code: int = 11611
    Msg: str = "Template not found or not accessible"
