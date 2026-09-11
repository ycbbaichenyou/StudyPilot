class DocumentParsingError(Exception):
    """A safe, user-visible description of a document parsing failure."""


class UnsupportedDocumentTypeError(DocumentParsingError):
    """Raised when no Stage 3-3 parser exists for a document type."""


class DocumentParsingPersistenceError(RuntimeError):
    """Raised when the parsing result status cannot be persisted safely."""
