"""Fixed-data readiness contract; missing artifacts remain explicit blockers."""

from __future__ import annotations


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


def readiness_payload() -> dict:
    return {
        "version": "1.32.00",
        "manifestStatus": "blocked",
        "reason": "No authoritative versioned private test-data package with measured hashes and spatial metadata was supplied. Values are not fabricated.",
        "required": [{"id": item[0], "file": item[1], "purpose": item[2], "status": "missing"} for item in REQUIRED_DATA],
    }

