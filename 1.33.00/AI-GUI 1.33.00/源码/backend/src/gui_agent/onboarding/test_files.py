"""Validation for project-owned test inputs without exposing file contents to planners."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path


ALLOWED_EXTENSIONS = {
    ".json", ".geojson", ".zip", ".hgt", ".png", ".jpg", ".jpeg",
    ".kml", ".kmz", ".shp", ".dbf", ".shx", ".prj", ".tif", ".tiff", ".csv",
}


def validation_profile(filename: str, requested: str | None = None) -> str:
    if requested and requested != "auto":
        return requested
    extension = Path(filename).suffix.lower()
    if extension == ".json": return "json"
    if extension == ".geojson": return "geojson"
    if extension in {".zip", ".kmz"}: return "zip"
    if extension == ".hgt": return "hgt"
    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}: return "image"
    if extension in {".kml", ".shp", ".dbf", ".shx", ".prj"}: return "gis"
    if extension == ".csv": return "csv"
    return "binary"


def validate_test_file(path: Path, filename: str, profile: str) -> list[str]:
    extension = Path(filename).suffix.lower()
    errors: list[str] = []
    if extension not in ALLOWED_EXTENSIONS:
        errors.append(f"不支持的文件扩展名：{extension or '(无扩展名)'}")
        return errors
    try:
        if profile in {"json", "geojson"}:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, (dict, list)):
                errors.append("JSON 顶层必须是对象或数组")
            if profile == "geojson" and (not isinstance(payload, dict) or payload.get("type") not in {
                "Feature", "FeatureCollection", "Point", "MultiPoint", "LineString",
                "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection",
            }):
                errors.append("GeoJSON 缺少合法 type")
        elif profile == "zip":
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                if bad:
                    errors.append(f"ZIP 条目 CRC 校验失败：{bad}")
                if not archive.namelist():
                    errors.append("ZIP 不能为空")
        elif profile == "hgt":
            if path.stat().st_size not in {1201 * 1201 * 2, 3601 * 3601 * 2}:
                errors.append("HGT 大小必须对应 1201x1201 或 3601x3601 高程网格")
        elif profile == "image":
            header = path.read_bytes()[:12]
            if extension == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
                errors.append("PNG 文件签名无效")
            if extension in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
                errors.append("JPEG 文件签名无效")
        elif profile == "csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                if not next(csv.reader(stream), None):
                    errors.append("CSV 不能为空")
        elif profile == "gis" and extension == ".kml":
            if b"<kml" not in path.read_bytes()[:4096].lower():
                errors.append("KML 根元素无效")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"{profile} 内容校验失败：{exc}")
    return errors
