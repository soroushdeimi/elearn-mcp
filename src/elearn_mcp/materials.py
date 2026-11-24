"""Bulk course materials download with manifest tracking."""

import json
import os
import pathlib
import re

import httpx

from . import course, moodle
from . import extract as extract_mod

_BAD_CHARS = re.compile(r'[/\\:*?"<>|\r\n]')


def safe(name: str) -> str:
    name = _BAD_CHARS.sub("_", name).strip(" .")
    return name[:80] or "untitled"


def _load_manifest(dest_dir: pathlib.Path) -> list[dict]:
    manifest = dest_dir / "manifest.json"
    if not manifest.exists():
        return []
    return json.loads(manifest.read_text(encoding="utf-8")).get("files", [])


def _save(dest_dir: pathlib.Path, manifest: dict):
    tmp = dest_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dest_dir / "manifest.json")


def download_course(
    session,
    course_id: int,
    dest_dir,
    modules=("resource", "folder"),
    extract: bool = True,
) -> dict:
    dest_dir = pathlib.Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "course_id": course_id,
        "files": _load_manifest(dest_dir),
        "errors": [],
        "downloaded": 0,
        "skipped": 0,
    }
    files, errors = manifest["files"], manifest["errors"]
    for section in course.contents(session, course_id):
        for item in section["items"]:
            if not item["visible"] or item["module"] not in modules:
                continue
            section_dir = dest_dir / f"{section['number']:02d} {safe(section['title'])}"
            try:
                existing = [
                    f for f in files if f["cmid"] == item["cmid"] and f["url"] == item["url"]
                ]
                if existing and all((dest_dir / f["path"]).exists() for f in existing):
                    manifest["skipped"] += 1
                    _save(dest_dir, manifest)
                    continue
                if existing:  # files missing on disk: re-download, drop stale entries
                    for f in existing:
                        files.remove(f)
                if item["module"] == "folder":
                    target = section_dir / safe(item["name"])
                else:
                    target = section_dir
                # collect entries locally so a mid-folder failure leaves no partial item
                new_entries = []
                for path in course.download_module(session, item["cmid"], item["module"], target):
                    text_path = None
                    if extract and path.suffix.lower() in extract_mod.SUPPORTED:
                        try:
                            text_path = (
                                extract_mod.write_text_copy(path).relative_to(dest_dir).as_posix()
                            )
                        except Exception as e:
                            errors.append(
                                {"cmid": item["cmid"], "item": item["name"], "error": str(e)}
                            )
                    new_entries.append(
                        {
                            "section": section["title"],
                            "item": item["name"],
                            "cmid": item["cmid"],
                            "module": item["module"],
                            "path": path.relative_to(dest_dir).as_posix(),
                            "url": item["url"],
                            "bytes": path.stat().st_size,
                            "text_path": text_path,
                            "text_version": extract_mod.VERSION if text_path else None,
                        }
                    )
                    manifest["downloaded"] += 1
                files.extend(new_entries)
                _save(dest_dir, manifest)
            except (moodle.MoodleError, ValueError, httpx.HTTPError) as e:
                if isinstance(e, moodle.SessionExpired):
                    _save(dest_dir, manifest)
                    raise
                errors.append({"cmid": item["cmid"], "item": item["name"], "error": str(e)})
                _save(dest_dir, manifest)
    _save(dest_dir, manifest)
    return manifest


def update_text_copy(dest_dir, path: str) -> str:
    """Rebuild the text copy of one manifest file with the current extractor."""
    dest_dir = pathlib.Path(dest_dir)
    out = extract_mod.write_text_copy(dest_dir / path)
    manifest = list_materials(dest_dir)
    for entry in manifest["files"]:
        if entry["path"] == path:
            entry["text_path"] = out.relative_to(dest_dir).as_posix()
            entry["text_version"] = extract_mod.VERSION
    if manifest["files"]:
        _save(dest_dir, manifest)
    return out.read_text(encoding="utf-8")


def list_materials(dest_dir) -> dict:
    manifest = pathlib.Path(dest_dir) / "manifest.json"
    if not manifest.exists():
        return {"files": [], "errors": []}
    return json.loads(manifest.read_text(encoding="utf-8"))
