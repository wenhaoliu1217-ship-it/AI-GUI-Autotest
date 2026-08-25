"""Fixed-data readiness contract; missing artifacts remain explicit blockers.

The acceptance suite must never infer that a file is safe to upload from its
filename alone.  A user-supplied manifest records measured bytes, SHA-256
hashes and bounded spatial metadata; this module validates that contract and
then checks the files on disk.  It deliberately does not inspect or store
credentials, cookies, tokens or raw site identity data.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


REQUIRED_DATA = [
    ("D01", "cesium-e2e-model.glb", "glTF model"),
    ("D02", "cesium-e2e-model-with-textures.zip", "model with textures"),
    ("D03", "cesium-e2e-tileset.zip", "3D Tiles ZIP"),
    ("D04", "cesium-e2e-pointcloud.laz", "point cloud"),
    ("D05", "cesium-e2e-imagery.tif", "GeoTIFF imagery"),
    ("D06", "cesium-e2e-terrain.tif", "terrain raster"),
    ("D07", "cesium-e2e.czml", "time-dynamic CZML"),
    ("D08", "cesium-e2e.kml", "KML"),
    ("D09", "cesium-e2e.geojson", "GeoJSON"),
    ("D10", "cesium-e2e-building.ifc", "BIM/CAD"),
    ("D11", "cesium-e2e-photogrammetry.zip", "photogrammetry"),
    ("D12", "cesium-e2e-sidecars.zip", "raster sidecars"),
    ("N01", "empty.glb", "empty file"),
    ("N02", "malformed-tileset.zip", "malformed tileset"),
    ("N03", "zip-slip-path.zip", "path traversal archive"),
    ("N04", "missing-texture.zip", "missing texture"),
    ("N05", "invalid.geojson", "invalid GeoJSON"),
    ("N06", "invalid.kml", "invalid KML"),
    ("N07", "unsupported.exe", "unsupported type"),
    ("N08", "zero-byte.tif", "zero-byte raster"),
    ("N09", "oversize.bin", "oversize fixture"),
    ("N10", "huge-feature-count.geojson", "large feature count"),
]

MANIFEST_FILENAME = "manifest.json"
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SPATIAL_METADATA_REQUIRED = {item[0] for item in REQUIRED_DATA if item[0].startswith("D")}
_REQUIRED_BY_ID = {item[0]: item for item in REQUIRED_DATA}


class CesiumTestDataManifestError(ValueError):
    """Raised when a supplied test-data manifest is not safe to install."""


def _safe_relative_filename(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CesiumTestDataManifestError("manifest file must be a non-empty relative filename")
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or len(path.parts) != 1:
        raise CesiumTestDataManifestError(f"manifest file path is not a safe package filename: {value}")
    return path.name


def validate_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a fixed 1.33.00 data manifest.

    The returned object only contains bounded metadata.  It is safe to write
    to the local data directory after this function succeeds.
    """

    if not isinstance(payload, dict):
        raise CesiumTestDataManifestError("test-data manifest must be a JSON object")
    if payload.get("version") != "1.33.00":
        raise CesiumTestDataManifestError("test-data manifest version must be 1.33.00")
    if payload.get("target") != "https://ion.cesium.com":
        raise CesiumTestDataManifestError("test-data manifest target must be https://ion.cesium.com")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(REQUIRED_DATA):
        raise CesiumTestDataManifestError(f"test-data manifest must contain exactly {len(REQUIRED_DATA)} artifacts")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(artifacts, start=1):
        if not isinstance(raw, dict):
            raise CesiumTestDataManifestError(f"artifact {index} must be a JSON object")
        artifact_id = raw.get("id")
        if artifact_id not in _REQUIRED_BY_ID:
            raise CesiumTestDataManifestError(f"artifact {index} has an unknown id")
        if artifact_id in seen:
            raise CesiumTestDataManifestError(f"artifact id is duplicated: {artifact_id}")
        seen.add(artifact_id)
        expected_id, expected_file, expected_purpose = _REQUIRED_BY_ID[artifact_id]
        filename = _safe_relative_filename(raw.get("file"))
        if filename != expected_file:
            raise CesiumTestDataManifestError(f"{artifact_id} must use filename {expected_file}")
        digest = raw.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise CesiumTestDataManifestError(f"{artifact_id} must provide a 64-character SHA-256")
        byte_size = raw.get("bytes")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
            raise CesiumTestDataManifestError(f"{artifact_id} bytes must be a non-negative integer")
        spatial = raw.get("spatialMetadata")
        if artifact_id in _SPATIAL_METADATA_REQUIRED:
            if not isinstance(spatial, dict) or not str(spatial.get("coordinateSystem", "")).strip():
                raise CesiumTestDataManifestError(f"{artifact_id} must provide spatialMetadata.coordinateSystem")
            bbox = spatial.get("bbox")
            if bbox is not None and (
                not isinstance(bbox, list)
                or len(bbox) not in {4, 6}
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox)
            ):
                raise CesiumTestDataManifestError(f"{artifact_id} spatialMetadata.bbox must contain 4 or 6 numbers")
        normalized_item = {
            "id": expected_id,
            "file": filename,
            "purpose": expected_purpose,
            "sha256": digest.lower(),
            "bytes": byte_size,
        }
        if isinstance(spatial, dict):
            normalized_item["spatialMetadata"] = {
                "coordinateSystem": str(spatial.get("coordinateSystem", ""))[:120],
                "bbox": spatial.get("bbox"),
            }
        normalized.append(normalized_item)

    missing = sorted(set(_REQUIRED_BY_ID) - seen)
    if missing:
        raise CesiumTestDataManifestError(f"test-data manifest is missing artifacts: {', '.join(missing)}")
    return {
        "version": "1.33.00",
        "target": "https://ion.cesium.com",
        "artifacts": sorted(normalized, key=lambda item: item["id"]),
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readiness_payload(root: Path | None = None) -> dict[str, Any]:
    """Return actionable manifest/file readiness without fabricating a pass."""

    base = root.resolve() if root is not None else None
    manifest_path = base / MANIFEST_FILENAME if base else None
    required = [
        {"id": item[0], "file": item[1], "purpose": item[2], "status": "missing"}
        for item in REQUIRED_DATA
    ]
    base_payload: dict[str, Any] = {
        "version": "1.33.00",
        "manifestStatus": "blocked",
        "manifestFile": MANIFEST_FILENAME,
        "reason": "No authoritative versioned private test-data package with measured hashes and spatial metadata was supplied. Values are not fabricated.",
        "required": required,
        "summary": {"required": len(required), "ready": 0, "missing": len(required), "mismatch": 0},
    }
    if manifest_path is None or not manifest_path.is_file():
        return base_payload
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = validate_manifest_payload(raw)
    except (OSError, ValueError, TypeError) as exc:
        base_payload["reason"] = f"Test-data manifest is invalid: {str(exc)[:500]}"
        base_payload["manifestStatus"] = "invalid"
        return base_payload

    by_id = {item["id"]: item for item in manifest["artifacts"]}
    ready = 0
    missing = 0
    mismatch = 0
    checked: list[dict[str, Any]] = []
    for artifact_id, filename, purpose in REQUIRED_DATA:
        item = by_id[artifact_id]
        path = (base / filename).resolve()
        if base not in path.parents:
            status = "unsafe_path"
            mismatch += 1
        elif not path.is_file():
            status = "missing"
            missing += 1
        else:
            actual_bytes = path.stat().st_size
            actual_sha256 = _hash_file(path)
            if actual_bytes != item["bytes"] or actual_sha256 != item["sha256"]:
                status = "hash_or_size_mismatch"
                mismatch += 1
            else:
                status = "ready"
                ready += 1
        checked.append({"id": artifact_id, "file": filename, "purpose": purpose, "status": status})
    base_payload["required"] = checked
    base_payload["summary"] = {"required": len(checked), "ready": ready, "missing": missing, "mismatch": mismatch}
    if ready == len(checked):
        base_payload["manifestStatus"] = "ready"
        base_payload["reason"] = "All fixed test-data files match the supplied byte sizes and SHA-256 hashes."
    else:
        base_payload["reason"] = "Manifest is structurally valid, but one or more fixed test-data files are missing or do not match their measured hashes."
    return base_payload
