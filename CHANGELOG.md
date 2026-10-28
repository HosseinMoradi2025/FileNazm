# Changelog

## 1.1.0 - 2026-10-27

### Added
- Internal YAML configuration loader/dumper using the Python standard library.
- Internal DOCX/PDF text extraction using the standard library.
- Native Windows tray implementation using Shell_NotifyIconW.
- Native folder watcher using ReadDirectoryChangesW.
- Native Recycle Bin support using SHFileOperationW.
- Internal raster/GIF preview engine replacing Pillow.
- Persian keyboard shortcut support for Ctrl+Z / Ctrl+Y.
- Atomic configuration saving with safer temporary-file cleanup.

### Changed
- Removed runtime third-party Python dependencies.
- Improved file-operation safety for overwrite, delete, undo, and cross-volume moves.
- Installer upgrade path preserved from version 1.0.0.

### Security
- Bounded parsing for YAML, PDF, DOCX, and ZIP/XML inputs.
- Fail-closed handling for unsupported encrypted or malformed PDFs.
- Protection against XML entity declarations in DOCX parsing.
- Reduced dependency surface by replacing external packages with standard-library / Win32 implementations.