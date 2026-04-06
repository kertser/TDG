"""
Elevation / Height Map Analyzer.

Fetches elevation data for cell centroids.

**Primary — Copernicus DEM 30m COG tiles via rasterio:**
  Reads Cloud-Optimized GeoTIFF tiles from AWS S3 using HTTP range requests.
  No rate limits, no auth, 30m resolution.  Typically finishes 65 000 points
  in a few seconds (1–2 tiles, one windowed read each).

**Fallback — Open-Meteo Elevation API:**
  Sequential requests with throttling and exponential back-off on HTTP 429.
  Used only when rasterio / GDAL is not installed.

Computes slope and aspect from neighboring cell elevations using finite
differences (Zevenbergen–Thorne method).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx

logger = logging.getLogger(__name__)

# ── Rasterio availability flag ────────────────────────────────
_HAS_RASTERIO = False
try:
    import rasterio                    # noqa: F401
    import numpy as np                 # noqa: F401
    _HAS_RASTERIO = True
except ImportError:
    pass

# ── Open-Meteo (API fallback) ────────────────────────────────
OPEN_METEO_URL = "https://api.open-meteo.com/v1/elevation"
OPEN_METEO_BATCH = 100
OPEN_METEO_TIMEOUT = 30

# ── Open-Elevation (last-resort fallback) ─────────────────────
OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
OPEN_ELEVATION_BATCH = 80
OPEN_ELEVATION_TIMEOUT = 30

# ── Throttle / retry settings (API path only) ────────────────
REQUEST_GAP_S = 1.2
MAX_RETRIES = 5
INITIAL_BACKOFF_S = 5.0
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF_S = 120.0
MAX_CONSECUTIVE_429 = 3


class _RateLimited(Exception):
    """Raised when the API returns 429."""


# ═══════════════════════════════════════════════════════════════
#  PRIMARY: Copernicus DEM 30 m via rasterio / COG
# ═══════════════════════════════════════════════════════════════

def _fetch_elevations_rasterio(
    centroids: list[tuple[str, float, float]],
    progress_callback=None,
) -> dict[str, float]:
    """
    Read elevations from Copernicus DEM 30 m COG tiles on AWS S3.

    Groups points by tile, opens each tile once, reads a single bounding
    window covering all points in the tile, then indexes individual pixel
    values from the numpy array.  Typically 1–2 HTTP range requests per
    tile for a 30 km grid.

    This function is synchronous (rasterio is not async-friendly) but
    very fast — seconds, not minutes.
    """
    import rasterio
    from rasterio.windows import Window
    import numpy as np
    from backend.services.terrain_analysis.srtm_tiles import tile_url

    t0 = time.time()

    # Group centroids by tile URL
    tile_groups: dict[str, list[tuple[str, float, float]]] = {}
    for snail_path, lat, lon in centroids:
        url = tile_url(lat, lon)
        tile_groups.setdefault(url, []).append((snail_path, lat, lon))

    results: dict[str, float] = {}
    tiles_ok = 0
    tiles_fail = 0

    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_TIMEOUT=60,
        GDAL_HTTP_MAX_RETRY=3,
        GDAL_HTTP_RETRY_DELAY=2,
    )

    with env:
        for tile_idx, (url, points) in enumerate(tile_groups.items()):
            try:
                with rasterio.open(url) as src:
                    nodata = src.nodata

                    # Convert all (lon, lat) → (row, col) pixel coords
                    rows_cols = []
                    for snail_path, lat, lon in points:
                        try:
                            r, c = src.index(lon, lat)
                            rows_cols.append((snail_path, r, c))
                        except Exception:
                            pass  # point outside tile extent

                    if not rows_cols:
                        tiles_ok += 1
                        continue

                    # Compute bounding pixel window
                    all_rows = [rc[1] for rc in rows_cols]
                    all_cols = [rc[2] for rc in rows_cols]
                    min_row, max_row = min(all_rows), max(all_rows)
                    min_col, max_col = min(all_cols), max(all_cols)

                    # Clamp to dataset bounds
                    min_row = max(0, min_row)
                    min_col = max(0, min_col)
                    max_row = min(src.height - 1, max_row)
                    max_col = min(src.width - 1, max_col)

                    win = Window(
                        col_off=min_col,
                        row_off=min_row,
                        width=max_col - min_col + 1,
                        height=max_row - min_row + 1,
                    )

                    # Single read for the whole window
                    data = src.read(1, window=win).astype(np.float32)

                    for snail_path, r, c in rows_cols:
                        # Translate to window-local coords
                        lr = r - min_row
                        lc = c - min_col
                        if 0 <= lr < data.shape[0] and 0 <= lc < data.shape[1]:
                            val = float(data[lr, lc])
                            if nodata is not None and val == nodata:
                                val = 0.0
                            if math.isnan(val) or math.isinf(val):
                                val = 0.0
                            results[snail_path] = val

                    tiles_ok += 1

            except Exception as exc:
                tiles_fail += 1
                logger.warning(f"Failed to read DEM tile {url}: {exc}")

            if progress_callback:
                progress_callback(tile_idx + 1, len(tile_groups))

    elapsed = time.time() - t0
    logger.info(
        f"Rasterio DEM: {len(results)}/{len(centroids)} points from "
        f"{tiles_ok} tiles ({tiles_fail} failed) in {elapsed:.1f}s"
    )
    return results


# ═══════════════════════════════════════════════════════════════
#  FALLBACK A: Open-Meteo API (sequential + throttled)
# ═══════════════════════════════════════════════════════════════

async def _fetch_open_meteo_batch(
    client: httpx.AsyncClient,
    points: list[tuple[str, float, float]],
) -> dict[str, float]:
    """Single Open-Meteo batch. Raises _RateLimited on 429."""
    if not points:
        return {}

    lats = ",".join(f"{p[1]:.6f}" for p in points)
    lons = ",".join(f"{p[2]:.6f}" for p in points)

    resp = await client.get(
        OPEN_METEO_URL,
        params={"latitude": lats, "longitude": lons},
    )

    if resp.status_code == 429:
        raise _RateLimited(f"429: {resp.text[:200]}")

    if resp.status_code != 200:
        logger.warning(f"Open-Meteo {resp.status_code}: {resp.text[:200]}")
        return {}

    data = resp.json()
    result: dict[str, float] = {}
    for i, elev in enumerate(data.get("elevation", [])):
        if i < len(points) and elev is not None:
            e = float(elev)
            result[points[i][0]] = 0.0 if math.isnan(e) else e
    return result


async def _fetch_elevations_open_meteo(
    centroids: list[tuple[str, float, float]],
    progress_callback=None,
) -> dict[str, float]:
    """Sequential Open-Meteo with throttle + exponential back-off on 429."""
    all_results: dict[str, float] = {}
    batches = [
        centroids[i:i + OPEN_METEO_BATCH]
        for i in range(0, len(centroids), OPEN_METEO_BATCH)
    ]
    total = len(batches)
    success = 0
    consecutive_429 = 0

    logger.info(f"Open-Meteo elevation: {len(centroids)} pts, {total} batches, {REQUEST_GAP_S}s gap")

    async with httpx.AsyncClient(timeout=OPEN_METEO_TIMEOUT) as client:
        for idx, batch in enumerate(batches):
            backoff = INITIAL_BACKOFF_S
            result: dict[str, float] = {}

            for attempt in range(MAX_RETRIES):
                try:
                    result = await _fetch_open_meteo_batch(client, batch)
                    consecutive_429 = 0
                    break
                except _RateLimited:
                    consecutive_429 += 1
                    if consecutive_429 >= MAX_CONSECUTIVE_429 and attempt >= 1:
                        break
                    wait = min(backoff, MAX_BACKOFF_S)
                    logger.info(
                        f"Open-Meteo 429 batch {idx+1}/{total} "
                        f"attempt {attempt+1} — wait {wait:.0f}s"
                    )
                    await asyncio.sleep(wait)
                    backoff *= BACKOFF_MULTIPLIER
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    logger.warning(f"Open-Meteo batch {idx+1} error: {exc}")
                    await asyncio.sleep(min(backoff, MAX_BACKOFF_S))
                    backoff *= BACKOFF_MULTIPLIER
                except Exception as exc:
                    logger.warning(f"Open-Meteo batch {idx+1} unexpected: {exc}")
                    break

            if result:
                all_results.update(result)
                success += 1

            if progress_callback:
                progress_callback(idx + 1, total)

            if consecutive_429 >= MAX_CONSECUTIVE_429:
                logger.warning("Open-Meteo rate-limit ceiling — aborting")
                break

            if idx < total - 1:
                await asyncio.sleep(REQUEST_GAP_S)

    logger.info(f"Open-Meteo: {len(all_results)}/{len(centroids)} pts ({success}/{total} batches)")
    return all_results


# ═══════════════════════════════════════════════════════════════
#  FALLBACK B: Open-Elevation API
# ═══════════════════════════════════════════════════════════════

async def _fetch_open_elevation_batch(
    client: httpx.AsyncClient,
    points: list[tuple[str, float, float]],
) -> dict[str, float]:
    if not points:
        return {}
    locations = [{"latitude": lat, "longitude": lon} for _, lat, lon in points]
    resp = await client.post(OPEN_ELEVATION_URL, json={"locations": locations})
    if resp.status_code == 429:
        logger.warning("Open-Elevation 429")
        return {}
    if resp.status_code != 200:
        logger.warning(f"Open-Elevation {resp.status_code}: {resp.text[:200]}")
        return {}
    result: dict[str, float] = {}
    for i, item in enumerate(resp.json().get("results", [])):
        if i < len(points):
            elev = item.get("elevation")
            if elev is not None:
                result[points[i][0]] = float(elev)
    return result


# ═══════════════════════════════════════════════════════════════
#  PUBLIC API — tries rasterio → Open-Meteo → Open-Elevation
# ═══════════════════════════════════════════════════════════════

async def fetch_elevations_concurrent(
    centroids: list[tuple[str, float, float]],
    progress_callback=None,
) -> dict[str, float]:
    """
    Fetch elevations for all centroids.

    Strategy:
      1. **Rasterio / Copernicus DEM COG** — instant batch reads (primary).
      2. **Open-Meteo API** — sequential with throttle (fallback if no rasterio).
      3. **Open-Elevation API** — last resort for any remaining gaps.

    Args:
        centroids: list of (snail_path, lat, lon)
        progress_callback: optional callable(done_count, total_count)

    Returns:
        dict[snail_path → elevation_m]
    """
    if not centroids:
        return {}

    all_results: dict[str, float] = {}
    source = "none"

    # ── 1. Try rasterio (Copernicus DEM 30 m) ────────────
    if _HAS_RASTERIO:
        try:
            # rasterio is synchronous — run in a thread to avoid blocking
            loop = asyncio.get_running_loop()
            all_results = await loop.run_in_executor(
                None,
                _fetch_elevations_rasterio,
                centroids,
                progress_callback,
            )
            source = "rasterio"
        except Exception as exc:
            logger.warning(f"Rasterio DEM failed: {exc}")
    else:
        logger.info("rasterio not available — falling back to Open-Meteo API")

    # ── 2. Fill gaps with Open-Meteo if rasterio got <80 % ──
    if len(all_results) < len(centroids) * 0.8:
        missing = [c for c in centroids if c[0] not in all_results]
        if missing:
            if source == "rasterio":
                logger.info(
                    f"Rasterio got {len(all_results)}/{len(centroids)} — "
                    f"filling {len(missing)} gaps via Open-Meteo"
                )
            api_results = await _fetch_elevations_open_meteo(missing, progress_callback)
            all_results.update(api_results)
            if not source or source == "none":
                source = "open-meteo"

    # ── 3. Last-resort: Open-Elevation for big gaps ───────
    if len(all_results) < len(centroids) * 0.3:
        missing = [c for c in centroids if c[0] not in all_results]
        if missing:
            logger.warning(f"Trying Open-Elevation for {len(missing)} remaining points...")
            fb_batches = [
                missing[i:i + OPEN_ELEVATION_BATCH]
                for i in range(0, len(missing), OPEN_ELEVATION_BATCH)
            ]
            async with httpx.AsyncClient(timeout=OPEN_ELEVATION_TIMEOUT) as fb_client:
                for batch in fb_batches:
                    try:
                        fb = await _fetch_open_elevation_batch(fb_client, batch)
                    except Exception:
                        fb = {}
                    all_results.update(fb)
                    if not fb:
                        logger.warning("Open-Elevation stopped (error)")
                        break
                    await asyncio.sleep(1.5)

    logger.info(
        f"Elevation total: {len(all_results)}/{len(centroids)} points "
        f"(source: {source})"
    )
    return all_results


# ── Legacy alias ──────────────────────────────────────────────

async def fetch_elevations(
    centroids: list[tuple[str, float, float]],
) -> dict[str, float]:
    """Alias — calls the main fetcher."""
    return await fetch_elevations_concurrent(centroids)


# ── Slope / aspect computation ───────────────────────────────

def compute_slope_aspect(
    elevations: dict[str, float],
    neighbors: dict[str, dict[str, str | None]],
    cell_size_m: float = 333.0,
) -> dict[str, dict]:
    """
    Compute slope and aspect for each cell from neighbor elevations.

    Uses finite differences on a 3×3 neighborhood (Zevenbergen-Thorne method).

    Args:
        elevations: dict[snail_path → elevation_m]
        neighbors: dict[snail_path → {n, s, e, w: snail_path | None}]
        cell_size_m: approximate cell side length in meters

    Returns:
        dict[snail_path → {elevation_m, slope_deg, aspect_deg}]
    """
    results: dict[str, dict] = {}

    for snail, elev in elevations.items():
        nb = neighbors.get(snail, {})

        # Get neighbor elevations (use self if neighbor missing)
        e_n = elevations.get(nb.get("n") or "", elev)
        e_s = elevations.get(nb.get("s") or "", elev)
        e_e = elevations.get(nb.get("e") or "", elev)
        e_w = elevations.get(nb.get("w") or "", elev)

        # Finite differences for slope
        dz_dx = (e_e - e_w) / (2 * cell_size_m)
        dz_dy = (e_n - e_s) / (2 * cell_size_m)

        slope_rad = math.atan(math.sqrt(dz_dx ** 2 + dz_dy ** 2))
        slope_deg = math.degrees(slope_rad)

        # Aspect (downhill direction)
        aspect_deg = None
        if abs(dz_dx) > 1e-8 or abs(dz_dy) > 1e-8:
            aspect_rad = math.atan2(-dz_dx, -dz_dy)
            aspect_deg = (math.degrees(aspect_rad) + 360) % 360

        results[snail] = {
            "elevation_m": elev,
            "slope_deg": round(slope_deg, 2),
            "aspect_deg": round(aspect_deg, 1) if aspect_deg is not None else None,
        }

    return results

