"""gcos: the three per-area views from the factory's ohca, and the cross-level consistency guards."""
import json
import types

import numpy as np
import pytest
import xarray as xr

import gcos


def test_config_record_consolidates_two_axes(tmp_path):
    b0700 = _blob(level="0_700", vol=7e14)
    b2000 = _blob(level="0_2000", vol=2e15)
    # 15_20 identical across both levels (collapses); each level lists its own constituents
    b0700.attrs.update({
        "localgp_ingest_run_config": json.dumps({"15_20":  {"var_name": "pt", "dir": "/15_20"},
                                                 "15_300": {"var_name": "pt", "dir": "/15_300"}}),
        "ohc_derive_run_facts": json.dumps({"constituents": ["15_20", "15_300"]}),
        "ohc_derive_code_version": "https://github.com/argovis/ohc_derive/commit/d",
    })
    b2000.attrs.update({
        "localgp_ingest_run_config": json.dumps({"15_20":   {"var_name": "pt", "dir": "/15_20"},
                                                 "300_700": {"var_name": "pt", "dir": "/300_700"}}),
        "ohc_derive_run_facts": json.dumps({"constituents": ["15_20", "300_700"]}),
        "ohc_derive_code_version": "https://github.com/argovis/ohc_derive/commit/d",
    })
    out = gcos.build_dataset([b0700, b2000], j_to_zj=1e-21, tag="G", provenance_link="http://g",
                             citation="Giglio et al. (2026)", product_name="LocalGP")
    cfg = types.SimpleNamespace(blobs=["d0.nc", "d1.nc"], tag="G", provenance_link="http://g",
                                j_to_zj=1e-21, code_version="https://x/commit/gggg", out=str(tmp_path),
                                product_name="LocalGP", author="Giglio_etal2026", citation="Giglio et al. (2026)")
    gcos.stamp_config_record(out, [b0700, b2000], cfg)

    # one consolidated attribute; no per-stage keys leaked (keeps compact storage)
    assert "config_record" in out.attrs
    assert not any(k.endswith(("_run_config", "_run_facts", "_code_version")) for k in out.attrs)

    rec = json.loads(out.attrs["config_record"])
    # localgp collapsed across levels -> keyed by constituent (15_20 appears once, not per-level)
    lg = rec["localgp_ingest"]["run_config"]
    assert lg["shared"] == {"var_name": "pt"}
    assert set(lg["per_constituent"]) == {"15_20", "15_300", "300_700"}
    # ohc_derive code_version agrees across levels -> bare value; run_facts genuinely per-level
    assert rec["ohc_derive"]["code_version"].endswith("/d")
    assert set(rec["ohc_derive"]["run_facts"]["per_level"]) == {"0_700", "0_2000"}
    # this step's own block
    assert rec["ohc_gcos_emitter"]["code_version"].endswith("gggg")
    assert set(rec["ohc_gcos_emitter"]["run_facts"]["levels"]) == {"0_700", "0_2000"}
    assert rec["ohc_gcos_emitter"]["run_config"]["tag"] == "G"
    # product_name/author ride in the config brick; citation does NOT (it has its own top-level attr)
    assert rec["ohc_gcos_emitter"]["run_config"]["product_name"] == "LocalGP"
    assert rec["ohc_gcos_emitter"]["run_config"]["author"] == "Giglio_etal2026"
    assert "citation" not in rec["ohc_gcos_emitter"]["run_config"]
    assert out.attrs["citation"] == "Giglio et al. (2026)"          # standalone top-level attr
    assert out.attrs["product_name"] == "LocalGP"                   # standalone top-level key


QUANTITY = {"name": "ohc", "kind": "extensive", "units": "J/m2", "long_name": "ocean heat content",
            "scale_terms": {"cp0": 3989.0, "rho0": 1030.0}, "publish_unit_factor": 1e12, "publish_units": "TJ/m^2"}


def _blob(level="0_2000", area=1e12, vol=1e15, window="2005-2024", with_sd=True, quantity=QUANTITY):
    """A synthetic ohc_derive blob: extensive ohca (TJ, baseline-referenced) + geometry + the quantity table."""
    ds = xr.Dataset({"ohca": ("year", np.array([1.0, 2.0]))}, coords={"year": [2005, 2006]})
    if with_sd:
        ds["ohca_sd"] = ("year", np.array([0.1, 0.2]))
    ds.attrs.update({"level": level, "area_m2": area, "volume_m3": vol, "time_window": window,
                     "quantity": json.dumps(quantity)})
    return ds


def test_three_views_from_ohca():
    out = gcos.build_dataset([_blob(area=1e12)], j_to_zj=1e-21, tag="dev", provenance_link=None)
    assert np.allclose(out["GCOS_0000_2000_OHCA_J_m2_oc"].values, [1.0, 2.0])   # ohca/area*1e12 (area 1e12)
    assert np.allclose(out["GCOS_0000_2000_OHCA_ZJ"].values, 1e-21 * 1e12 * np.array([1.0, 2.0]))
    assert np.allclose(out["GCOS_0000_2000_vol_ave_temp_anom"].values,
                       1e12 * np.array([1.0, 2.0]) / (3989.0 * 1030.0 * 1e15))
    assert out["GCOS_0000_2000_OHCA_J_m2_oc"].attrs["units"] == "J/m2"
    assert out.attrs["time_window"] == "2005-2024"


def test_sd_views_present_and_scaled():
    out = gcos.build_dataset([_blob(area=1e12)], 1e-21, "dev", None)
    assert np.allclose(out["GCOS_0000_2000_OHCA_J_m2_oc_sd"].values, [0.1, 0.2])
    assert np.allclose(out["GCOS_0000_2000_OHCA_ZJ_sd"].values, 1e-21 * 1e12 * np.array([0.1, 0.2]))


def test_mean_only_blob_has_no_sd():
    out = gcos.build_dataset([_blob(with_sd=False)], 1e-21, "dev", None)
    assert "GCOS_0000_2000_OHCA_J_m2_oc_sd" not in out.data_vars


def test_j_to_zj_petajoule_mode():
    out = gcos.build_dataset([_blob(area=1e12)], j_to_zj=1e-15, tag="dev", provenance_link=None)
    assert np.allclose(out["GCOS_0000_2000_OHCA_ZJ"].values, 1e-15 * 1e12 * np.array([1.0, 2.0]))


def test_multiple_levels_share_one_year_axis():
    out = gcos.build_dataset([_blob(level="0_700", vol=7e14), _blob(level="0_2000", vol=2e15)],
                             1e-21, "dev", None)
    assert "GCOS_0000_0700_OHCA_J_m2_oc" in out.data_vars
    assert "GCOS_0000_2000_OHCA_J_m2_oc" in out.data_vars
    assert list(out["years"].values) == [2005.0, 2006.0]


def test_year_axis_mismatch_errors():
    other = _blob(level="0_700").assign_coords(year=[2005, 2007])
    with pytest.raises(SystemExit):
        gcos.build_dataset([_blob(level="0_2000"), other], 1e-21, "dev", None)


def test_baseline_window_mismatch_errors():
    with pytest.raises(SystemExit):
        gcos.build_dataset([_blob(level="0_700", window="2005-2024"),
                            _blob(level="0_2000", window="2004-2024")], 1e-21, "dev", None)


def test_quantity_mismatch_errors():
    other = _blob(level="0_700", quantity=dict(QUANTITY, scale_terms={"cp0": 4000.0, "rho0": 1030.0}))
    with pytest.raises(SystemExit):
        gcos.build_dataset([_blob(level="0_2000"), other], 1e-21, "dev", None)


def test_physical_constants_come_from_the_quantity_scale_terms():
    assert gcos.physical_constants(_blob()) == (3989.0, 1030.0)
    with pytest.raises(SystemExit):                                  # no cp0/rho0: not a heat content
        gcos.physical_constants(_blob(quantity=dict(QUANTITY, name="mld", scale_terms={})))
    bare = _blob()
    del bare.attrs["quantity"]
    with pytest.raises(SystemExit):
        gcos.physical_constants(bare)


def test_filename():
    assert gcos.filename("dev", "2005_2006_tw2005_2024", "LocalGP", "Giglio_etal2026") == \
        "gcos_dev_2005_2006_tw2005_2024_LocalGP_Giglio_etal2026.nc"


def test_file_token_carries_data_span_and_baseline():
    assert gcos._file_token(np.array([2005, 2006]), "2005-2024") == "2005_2006_tw2005_2024"


def test_round_trip_through_files(tmp_path):
    src = str(tmp_path / "derive_dev_0_2000.nc")
    _blob().to_netcdf(src)
    blob = xr.open_dataset(src)
    out = gcos.build_dataset([blob], 1e-21, "dev", None, "Giglio et al. (2026)")
    dest = str(tmp_path / gcos.filename("dev", gcos._file_token(out["years"].values, out.attrs["time_window"]),
                                        "LocalGP", "Giglio_etal2026"))
    out.to_netcdf(dest)
    back = xr.open_dataset(dest)
    assert back.attrs["citation"] == "Giglio et al. (2026)"         # round-trips
    assert "GCOS_0000_2000_OHCA_ZJ" in back.data_vars
