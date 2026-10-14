"""Portable provenance and guards for read-only source data."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS = PROJECT_ROOT / "artifacts" / "gimcheon_gimbap_2026"


def artifact_path(directory: Path, relative: str) -> Path:
    """Reject symlink/path escapes before writing a generated file."""
    root = directory.expanduser().resolve()
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"Artifact path escapes its output directory: {relative}")
    return candidate


def output_directory(path: Path, source: Path | None = None, project: Path = PROJECT_ROOT) -> Path:
    """Validate before creating output; never write in data/img or around an input."""
    output = path.expanduser().resolve()
    originals = {(root.expanduser().resolve() / "data" / "img").resolve()
                 for root in (project, PROJECT_ROOT)}
    if any(output.is_relative_to(directory) for directory in originals):
        raise ValueError("Output must be outside data/img; original files are read only")
    if source is not None and source.expanduser().resolve().is_relative_to(output):
        raise ValueError("Output must not contain the source image; choose a separate directory")
    return output


def source_reference(source: Path, project: Path = PROJECT_ROOT) -> dict:
    """Preserve filesystem spelling, without embedding a developer's absolute path."""
    source = source.expanduser().resolve()
    project = project.expanduser().resolve()
    if source.is_relative_to(project):
        return {"path": source.relative_to(project).as_posix(), "path_base": "project_root"}
    return {"path": source.name, "path_base": "external_input"}


def resolve_source(reference: dict, project: Path = PROJECT_ROOT, explicit_input: Path | None = None) -> Path:
    if explicit_input is not None:
        return explicit_input.expanduser().resolve(strict=True)
    if reference.get("path_base") != "project_root":
        raise ValueError("External or legacy source references require --input")
    project = project.expanduser().resolve()
    relative = Path(reference["path"])
    candidate = (project / relative).resolve()
    if relative.is_absolute() or not candidate.is_relative_to(project):
        raise ValueError("Source reference must stay inside the project root")
    if not candidate.is_file():
        # Git/filesystems may round-trip Korean filenames as NFC or NFD.
        import unicodedata
        wanted = unicodedata.normalize("NFC", candidate.name)
        matches = [item for item in candidate.parent.iterdir()
                   if unicodedata.normalize("NFC", item.name) == wanted and item.is_file()]
        if len(matches) != 1:
            raise FileNotFoundError(f"Source image is missing or ambiguous: {relative}")
        candidate = matches[0].resolve()
        if not candidate.is_relative_to(project):
            raise ValueError("Source reference resolves outside the project root")
    return candidate
