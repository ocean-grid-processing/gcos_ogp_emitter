#!/usr/bin/env python3
"""GCOS/WMO-report packaging: ohc_derive blobs -> one combined GCOS deliverable.

The factory has done the analysis — the n_fac cross-layer combine, the annual mean, and the OHCA
baseline window. Each per-level blob carries `ohca` (annual anomaly, basin-integrated TJ, referenced
to its baseline window) plus `area_m2`, `volume_m3`, the `quantity` table (whose `scale_terms` hold
`cp0` and `rho0`), and the `time_window` it was built
with. This step packages one file spanning every level, expressing each level's anomaly three ways:

    GCOS_<lo>_<hi>_OHCA_J_m2_oc(y)      = ohca / area * 1e12                       [J/m^2]
    GCOS_<lo>_<hi>_OHCA_ZJ(y)           = j_to_zj * area * OHCA_J_m2_oc            [ZJ, see note]
    GCOS_<lo>_<hi>_vol_ave_temp_anom(y) = area * OHCA_J_m2_oc / (cp0*rho0*volume)  [degC]

Each gets a `*_sd` companion when the blob carried the ensemble — the factory's `ohca_sd` pushed
through the same deterministic factors. Note that `ohca_sd` is the spread of the *anomaly* members
(each demeaned by its own window), the factory's convention; the retired emitter used the spread of
the absolute yearly value instead.

NOTE on j_to_zj: the original uses 1e-15, which is J->PJ (petajoules), not J->ZJ (1e-21) — so its
`_ZJ` column is actually petajoules. We default to 1e-21 for genuine zettajoules (a deliberate
correction); pass 1e-15 to byte-match the collaborator's original file on that column.
"""
import argparse
import json
import os

import numpy as np
import xarray as xr

J_PER_TJ = 1e12

# This step's identity, used to key its block inside the consolidated `config_record`. GCOS is a
# cross-level fan-in (one file spans every level), so it groups each upstream block by level and folds
# the whole chain into ONE `config_record` attribute — one attribute keeps the file in HDF5 compact
# attribute storage (a dozen separate attrs tips it into dense/fractal-heap storage some netcdf builds
# mis-read). Blocks are opaque — parsed only to regroup, never read.
STAGE = "ohc_gcos_emitter"
_PROV_SUFFIXES = ("_run_config", "_run_facts", "_code_version")


def _compact(obj):
    """One-line JSON — reads as a single clean line in `ncdump -h`."""
    return json.dumps(obj, separators=(",", ":"), default=str)


def _maybe_json(v):
    """Parse an upstream block back to JSON so it nests as a real object; leave non-JSON (a bare
    code_version URL) as-is."""
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _shared_and_per(group_map):
    """{group: block} -> (shared, per): keys present in every group with an equal value go to `shared`;
    everything else stays per group. Lossless — block[g] == {**shared, **per[g]}."""
    groups = list(group_map)
    common = set(group_map[groups[0]])
    for g in groups[1:]:
        common &= set(group_map[g])
    shared = {}
    for k in sorted(common):
        vals = [group_map[g][k] for g in groups]
        if all(v == vals[0] for v in vals):
            shared[k] = vals[0]
    per = {g: {k: v for k, v in group_map[g].items() if k not in shared} for g in groups}
    return shared, per


def _compact_block(block, axis):
    """Factor one fan-out `{group: value}`: object values -> shared + per_<axis> (a fully-shared block
    collapses to the bare shared object); scalar values -> the bare value if all agree, else per_<axis>."""
    values = list(block.values())
    if all(isinstance(v, dict) for v in values):
        shared, per = _shared_and_per(block)
        if not any(per.values()):
            return shared
        return {"shared": shared, "per_" + axis: per}
    if all(v == values[0] for v in values):
        return values[0]
    return {"per_" + axis: dict(block)}


def _dry_part(by_level, constituents):
    """DRY one part collected across levels. A localgp part arrives as `{level: {constituent: block}}`
    and is really per-constituent (a constituent's block is level-independent), so the level axis
    collapses to shared + per_constituent. An ohc_derive part arrives as `{level: block}` and is
    genuinely per-level, so it becomes shared + per_level (or a bare value when the levels agree)."""
    values = list(by_level.values())
    if values and all(isinstance(v, dict) and v and set(v) <= constituents for v in values):
        flat, consistent = {}, True                          # {constituent: block}, level-independent
        for per_cons in by_level.values():
            for c, block in per_cons.items():
                if c in flat and flat[c] != block:
                    consistent = False
                flat[c] = block
        if consistent:
            return _compact_block(flat, "constituent")
        return {"per_level": {lv: _compact_block(pc, "constituent") for lv, pc in by_level.items()}}
    return _compact_block(by_level, "level")


def stamp_config_record(out, blobs, cfg):
    """Assemble the whole provenance chain into ONE `config_record` attribute, keyed by stage. Forwarded
    blocks are grouped by level then DRY'd (localgp collapses across levels to shared+per_constituent;
    ohc_derive to shared+per_level). One attribute keeps the file in HDF5 compact storage."""
    record = {}                                              # stage -> part -> {level: value}
    for blob in blobs:
        level = blob.attrs.get("level")
        for k, v in blob.attrs.items():
            for suffix in _PROV_SUFFIXES:
                if k.endswith(suffix):
                    record.setdefault(k[:-len(suffix)], {}).setdefault(suffix[1:], {})[level] = _maybe_json(v)
                    break
    # constituent roster: union across levels of ohc_derive.run_facts.constituents
    constituents = set()
    for facts in record.get("ohc_derive", {}).get("run_facts", {}).values():
        if isinstance(facts, dict) and isinstance(facts.get("constituents"), list):
            constituents |= set(facts["constituents"])
    for parts in record.values():
        for name, by_level in list(parts.items()):
            parts[name] = _dry_part(by_level, constituents)
    # this step's own block (singleton — no level axis). citation has its own top-level attr, so keep it
    # out of the brick (not duplicated); product_name/author stay in run_config for the record.
    record[STAGE] = {
        "run_config": {k: v for k, v in vars(cfg).items() if k != "citation"},
        "run_facts": {
            "levels": [blob.attrs.get("level") for blob in blobs],
            "time_window": out.attrs.get("time_window"),
            "j_to_zj": cfg.j_to_zj,
            "ensemble": any("ohca_sd" in blob.data_vars for blob in blobs),
            "source_blobs": [os.path.abspath(p) for p in cfg.blobs],
        },
        "code_version": cfg.code_version,
    }
    out.attrs["config_record"] = _compact(record)


def _band(level):
    """A level name "lo_hi" -> the zero-padded GCOS band token "0000_0300"."""
    lo, hi = (int(x) for x in level.split("_"))
    return "%04d_%04d" % (lo, hi)


def physical_constants(blob):
    """(cp0, rho0) from the blob's `quantity` table — the ingest scale terms that turned integrated
    temperature into heat content. A quantity without them can't be reported as a GCOS heat content."""
    if "quantity" not in blob.attrs:
        raise SystemExit("blob for level %s has no `quantity` attr (expected an ohc_derive blob)"
                         % blob.attrs.get("level"))
    terms = json.loads(blob.attrs["quantity"]).get("scale_terms", {})
    if "cp0" not in terms or "rho0" not in terms:
        raise SystemExit("level %s: the quantity's scale_terms lack cp0/rho0; GCOS needs the physical "
                         "constants (got %s)" % (blob.attrs.get("level"), sorted(terms)))
    return float(terms["cp0"]), float(terms["rho0"])


def build_dataset(blobs, j_to_zj, tag, provenance_link, citation="", product_name=""):
    """The combined GCOS Dataset over `years`, three views per level, from the factory blobs.

    Every blob must share the year axis, the baseline window, and the quantity table (the deliverable
    is one consistent set of levels); mismatches raise.
    """
    years = window = quantity = None
    data_vars = {}
    for blob in blobs:
        yrs = blob["ohca"]["year"].values.astype("int64")
        if years is None:
            years, window, quantity = yrs, blob.attrs["time_window"], blob.attrs["quantity"]
            cp0, rho0 = physical_constants(blob)
        else:
            if not np.array_equal(yrs, years):
                raise SystemExit("year axes differ across levels (at %s)" % blob.attrs["level"])
            if blob.attrs["time_window"] != window:
                raise SystemExit("baseline windows differ across levels (%s vs %s)"
                                 % (blob.attrs["time_window"], window))
            if blob.attrs.get("quantity") != quantity:
                raise SystemExit("quantity tables differ across levels (at %s)" % blob.attrs["level"])

        area, vol = float(blob.attrs["area_m2"]), float(blob.attrs["volume_m3"])
        band = _band(blob.attrs["level"])
        anom_jm2 = blob["ohca"].values / area * J_PER_TJ         # TJ -> J/m^2, float64

        data_vars["GCOS_%s_OHCA_J_m2_oc" % band] = xr.DataArray(
            anom_jm2, dims=("years",), attrs={"units": "J/m2", "GCOS_area": area})
        data_vars["GCOS_%s_OHCA_ZJ" % band] = xr.DataArray(
            j_to_zj * area * anom_jm2, dims=("years",), attrs={"units": "ZJ", "GCOS_area": area})
        data_vars["GCOS_%s_vol_ave_temp_anom" % band] = xr.DataArray(
            area * anom_jm2 / (cp0 * rho0 * vol), dims=("years",),
            attrs={"units": "degC", "GCOS_volume": vol})

        if "ohca_sd" in blob:
            jm2_sd = blob["ohca_sd"].values / area * J_PER_TJ
            data_vars["GCOS_%s_OHCA_J_m2_oc_sd" % band] = xr.DataArray(
                jm2_sd, dims=("years",), attrs={"units": "J/m2", "GCOS_area": area})
            data_vars["GCOS_%s_OHCA_ZJ_sd" % band] = xr.DataArray(
                j_to_zj * area * jm2_sd, dims=("years",),
                attrs={"units": "ZJ", "GCOS_area": area})
            data_vars["GCOS_%s_vol_ave_temp_anom_sd" % band] = xr.DataArray(
                area * jm2_sd / (cp0 * rho0 * vol), dims=("years",),
                attrs={"units": "degC", "GCOS_volume": vol})

    out = xr.Dataset(data_vars, coords={"years": ("years", years.astype("float64"))})
    out.attrs["time_window"] = window
    out.attrs["provenance_tag"] = tag
    if provenance_link is not None:
        out.attrs["provenance_link"] = provenance_link
    out.attrs["citation"] = citation
    if product_name:
        out.attrs["product_name"] = product_name   # top-level discoverable key (also in config_record)
    return out


def _file_token(years, window):
    """Combined filename token `<data>_tw<baseline>`: the data span (from the shared year axis) then the
    baseline window (always set for GCOS). Both `YYYY_YYYY`, e.g. `2005_2024_tw2005_2024`."""
    data = "%d_%d" % (int(years.min()), int(years.max()))
    return "%s_tw%s" % (data, window.replace("-", "_"))


def filename(tag, token, product_name, author):
    """gcos_<tag>_<data>_tw<baseline>_<product_name>_<author>.nc (product_name/author last before .nc)."""
    return "gcos_%s_%s_%s_%s.nc" % (tag, token, product_name, author)


def main():
    ap = argparse.ArgumentParser(description="GCOS packaging: ohc_derive blobs -> GCOS deliverable")
    ap.add_argument("blobs", nargs="+", help="ohc_derive outputs, one per synthetic level")
    ap.add_argument("--tag", required=True, help="provenance tag: filename token + provenance_tag attr")
    ap.add_argument("--provenance-link", default=None, help="URL/path to the provenance record")
    ap.add_argument("--code-version", required=True,
                    help="URL to the exact ohc_gcos_emitter code (commit/release); stamped as "
                         "ohc_gcos_emitter_code_version")
    ap.add_argument("--j-to-zj", default=1e-21, type=float,
                    help="OHCA_ZJ scale; 1e-21 = true zettajoules (default). Pass 1e-15 to byte-match "
                         "the original file, whose _ZJ column is actually petajoules.")
    ap.add_argument("--product-name", required=True,
                    help="product_name string, the first of the filename's trailing pair and in config_record "
                         "(e.g. LocalGP)")
    ap.add_argument("--author", required=True,
                    help="author string, the last of the filename's trailing pair and in config_record "
                         "(e.g. Giglio_etal2026)")
    ap.add_argument("--citation", required=True,
                    help="citation sentence; written to the top-level `citation` attr")
    ap.add_argument("--out", default=".")
    cfg = ap.parse_args()
    cfg.tag = "".join(cfg.tag.split())                           # whitespace-stripped, otherwise verbatim
    cfg.product_name = "".join(cfg.product_name.split())                   # filename tokens: whitespace-stripped,
    cfg.author = "".join(cfg.author.split())                     # case preserved, no other munging

    blobs = [xr.open_dataset(p) for p in cfg.blobs]
    for p, b in zip(cfg.blobs, blobs):
        if "ohca" not in b.data_vars:
            raise SystemExit("%s carries no ohca; run ohc_derive with --quantities ohca" % p)
        if b.attrs.get("time_window", "all") == "all":
            raise SystemExit("%s has no baseline window; GCOS needs ohc_derive run with --time-window" % p)
        physical_constants(b)                                    # errors if the quantity lacks cp0/rho0

    out = build_dataset(blobs, cfg.j_to_zj, cfg.tag, cfg.provenance_link, cfg.citation, cfg.product_name)
    stamp_config_record(out, blobs, cfg)                        # whole chain -> one config_record attr
    os.makedirs(cfg.out, exist_ok=True)
    dest = os.path.join(cfg.out, filename(cfg.tag, _file_token(out["years"].values, out.attrs["time_window"]),
                                          cfg.product_name, cfg.author))
    out.to_netcdf(dest, engine="netcdf4")
    print("wrote", dest)


if __name__ == "__main__":
    main()
