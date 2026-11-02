"""Give model callbacks verified existing files without changing role JSON APIs."""
from __future__ import annotations

from collections.abc import Callable

from ..contracts import CriteriaDraft, ExtractRequest, InspectionResult, InspectRequest
from ..files import VerifiedDocument, VerifiedPoster
from ..ports import FileAccess


class FileBackedExtractor:
    def __init__(self, files: FileAccess,
                 extract: Callable[[ExtractRequest, VerifiedDocument], CriteriaDraft]):
        self.files, self.extract_document = files, extract

    def extract(self, request: ExtractRequest) -> CriteriaDraft:
        document = self.files.read_document(request.context, request.document)
        return self.extract_document(request, document)


class FileBackedInspector:
    def __init__(self, files: FileAccess,
                 inspect: Callable[[InspectRequest, VerifiedPoster], InspectionResult]):
        self.files, self.inspect_poster = files, inspect

    def inspect(self, request: InspectRequest) -> InspectionResult:
        poster = self.files.read_asset(request.context, request.asset)
        return self.inspect_poster(request, poster)
