# gcos_ogp_emitter

`gcos_ogp_emitter` packages `ogp_derive` blobs into the GCOS/WMO-report deliverable — one NetCDF
spanning every level, `gcos_<tag>_<data>_tw<baseline>_<product_name>_<author>.nc`, where `<tag>` is the
provenance tag (whitespace-stripped, otherwise verbatim), `<data>` (`YYYY_YYYY`) is the data span,
`tw<baseline>` (`twYYYY_YYYY`) is the baseline-window years, and `<product_name>_<author>` is the
publication descriptor (e.g. `LocalGP_Giglio_etal2026`).

```
localgp_ogp_ingest ─▶ publish ─▶ ogp_derive (--quantities ohca --time-window 2005:2024) ─▶ gcos_ogp_emitter ─▶ GCOS .nc
```

The analysis is all upstream. `ogp_derive` does the `n_fac` cross-layer combine, the annual mean, and
the OHCA baseline window. Each per-level blob hands over `ohca` — the annual OHC anomaly,
basin-integrated (TJ), already referenced to the baseline window — plus `area_m2`, `volume_m3`, the
`quantity` table (whose `scale_terms` carry `cp0` and `rho0`), and the `time_window` it was built with.
This emitter is the packaging: express each level's anomaly three ways and assemble one file across
levels.

This emitter is OHC-specific by design: the three GCOS views only make sense for a heat content built
from `cp0·rho0`-scaled temperature, and the emitter refuses a blob whose `quantity` table lacks those
terms.

## What it computes

Per level, from `ohca` (TJ) and that level's `area` / `volume` / `cp0` / `rho0`:

```
GCOS_<lo>_<hi>_OHCA_J_m2_oc(y)      = ohca / area * 1e12                       # J/m²
GCOS_<lo>_<hi>_OHCA_ZJ(y)           = j_to_zj * area * OHCA_J_m2_oc            # ZJ (see below)
GCOS_<lo>_<hi>_vol_ave_temp_anom(y) = area * OHCA_J_m2_oc / (cp0*rho0*volume)  # degC
```

The baseline subtraction that defines the anomaly already happened in the factory (`--time-window`);
this step never re-subtracts. `<lo>_<hi>` are the level's dbar bounds, zero-padded
(`GCOS_0000_2000_…`). Every level lands in one file on a shared `years` axis, and the emitter errors
if the levels disagree on the year axis, the baseline window, or the quantity table.

**Error bars.** If the derive run kept the ensemble, each blob carries `ohca_sd`, and every view gets
a `*_sd` companion — the factory's yearly anomaly SD pushed through the same deterministic factors.
The SD is the spread of the **anomaly** members (each demeaned by its own window), the factory's
convention; the retired emitter used the spread of the absolute yearly value.

## Building the input

`gcos_ogp_emitter` consumes one `ogp_derive` blob per synthetic level, built along the LocalGP OHC
happy path in [`ogp_derive/examples/derive_ohc.slurm`](../ogp_derive/examples/derive_ohc.slurm) with
the GCOS baseline window and the ensemble on:

```bash
python ../ogp_derive/run.py OHC_<tag>*.nc \
    --levels levels/localgp.toml --level 0_2000 --time-window 2005:2024 \
    --quantities ohca --mask contiguous_from_top \
    --bathy etopo60.cdf --tag <tag> --code-version URL \
    --product-name LocalGP --author Giglio_etal2026 --citation "…" --out <dir>
```

`--quantities ohca` is all GCOS needs — the three views are all derived from it (the production
derive run asks for `ohca,ohu,ohca_trend,ohu_trend,map` so one blob feeds every emitter). Each blob
**must** carry:

- data var **`ohca`** (annual anomaly, TJ), and **`ohca_sd`** for the `_sd` columns (present when the
  derive run kept the ensemble);
- attrs **`area_m2`**, **`volume_m3`**, **`quantity`**, **`level`**, and **`time_window`** — a real
  window, not `"all"`, since a GCOS file is defined by its baseline.

The emitter errors on any missing piece. `cp0`/`rho0` are read from the `quantity` table's
`scale_terms` — the ingest factors that turned integrated temperature into heat content — so a
quantity without them can't be reported as a GCOS heat content, and the table must agree across the
levels.

## Usage

### Environment

See `Dockerfile` for a containerized environment; build the same into an anaconda env on blanca for
running on the CU cluster.

### Test

```bash
docker image build -t gcos_ogp_emitter:test .
docker container run -v $(pwd):/app gcos_ogp_emitter:test pytest
```

End-to-end validation is a separate exercise — reproduce
[this 2026 result](https://zenodo.org/records/18187866) and diff it (match `GCOS_area`/`GCOS_volume`
first, then the series) with [`parity.py`](parity.py), which reconciles the two intentional
differences (the `_ZJ` scale, and levels present in only one file).

### Run

The happy path is [`emit.slurm`](emit.slurm), which takes `<baseline_window>` and globs every level's
derive blob for that window out of the results directory; [`run.sh`](run.sh) loops it over the
windows of a release:

```bash
sbatch emit.slurm 2005_2024
```

or directly:

```bash
python gcos.py derive_<tag>_<data>_tw<baseline>_*.nc --tag <tag> --code-version URL \
    --product-name LocalGP --author Giglio_etal2026 --citation "…" [--provenance-link URL] [--j-to-zj 1e-21] [--out DIR]
```

Level selection is by which blobs you pass — every blob on the command line becomes a set of columns
in the one output file. `--product-name` / `--author` become the filename's trailing pair
(`…_<product_name>_<author>.nc`) and are recorded in `config_record`; `--citation` is written to a
standalone top-level `citation` attribute.

#### gcos.py options

All configuration is on the command line — no env, no config file. Every resolved option lands in
`config_record` (under this stage's `run_config`), except `--citation`, which has its own attr.

| option | required | default | what it does |
|---|:--:|---|---|
| `derive_*.nc` (positional, 1+) | **yes** | | `ogp_derive` blobs, one per synthetic level; each must carry `ohca`, a `quantity` table with `cp0`/`rho0`, and a real `time_window`. They all go into one file |
| `--tag` | **yes** | | provenance tag: the filename token (whitespace-stripped, case preserved, no other munging) and the `provenance_tag` attr. Should match the tag the blobs were derived under |
| `--code-version` | **yes** | | URL to the exact `gcos_ogp_emitter` code (commit/release); recorded as this stage's `code_version` inside `config_record` |
| `--product-name` | **yes** | | product_name string; first of the filename's trailing pair (whitespace-stripped, case preserved), a standalone top-level `product_name` attr, and recorded in `config_record` |
| `--author` | **yes** | | author string; last of the filename's trailing pair (e.g. `Giglio_etal2026`) and recorded in `config_record` |
| `--citation` | **yes** | | citation sentence; written to the standalone top-level `citation` attr (kept out of `config_record` so it isn't duplicated) |
| `--j-to-zj` | | `1e-21` | `OHCA_ZJ` scale — `1e-21` = true zettajoules; `1e-15` byte-matches the original's (mislabelled petajoule) `_ZJ` column |
| `--provenance-link` | | *(none)* | URL/path to the provenance record; written to the `provenance_link` attr |
| `--out` | | `.` | output directory (created if absent) |

## Output and provenance

Global attrs: `time_window`, `provenance_tag`, `provenance_link` (when given), `citation`,
`product_name`, and one `config_record`. Each data variable carries `units` and its level's
`GCOS_area` (the `J_m2_oc` and `ZJ` views) or `GCOS_volume` (the temperature view).

**Provenance chain.** GCOS combines every level into one file, so it is a cross-level fan-in. The whole
chain is folded into **one** `config_record` attribute keyed by stage:

```
config_record = {
  "localgp_ingest":  {"run_config": {shared + per_constituent}, …},   # collapsed across levels
  "localgp_publish": {…},
  "ohc_derive":      {"run_config": {shared + per_level}, …},         # genuinely per-level
  "ohc_gcos_emitter":{"run_config": {resolved args}, "run_facts": {levels, time_window, j_to_zj, ensemble, source_blobs}, "code_version": "…"}
}
```

The stage keys are the pipeline's provenance contract and predate the repo renames: `ohc_derive` is
written by `ogp_derive` and `ohc_gcos_emitter` by this emitter.

*Why one attribute:* many separate global attributes tip HDF5 into **dense (fractal-heap) attribute
storage**, whose layout some netcdf builds mis-read; a single attribute keeps the file at ≤ 8 global
attributes, i.e. **compact** storage, which every reader handles. `provenance_tag` / `provenance_link`
stay separate as the run's discoverable identity.

The forwarded blocks are DRY'd on both axes: the `localgp_*` blocks arrive as `{level: {constituent:
block}}` but a constituent's block is level-independent, so the level axis collapses to `shared` +
`per_constituent` (a constituent appears once, not once per level); the `ohc_derive_*` blocks are
genuinely per-level, so they become `shared` + `per_level` (or a bare value when the levels agree).
Lossless, driven by the `constituents` roster in `ohc_derive.run_facts`.

The `<data>` span in the filename comes from the blobs' shared year axis and `tw<baseline>` from their
`time_window`; the reference area and volume are the level's masked footprint as `ogp_derive` computed
them. Nothing about the levels, the window or the footprint is configured here.

## Notes

- **Reference area = the level's footprint** as masked in `ogp_derive` (`contiguous_from_top` for the
  LocalGP OHC path): the cells the level actually holds water in, under the same "shallowest
  contributor" convention as the original, now computed upstream.
- **Error bars are a linear sum** across constituents (`sd = Σ n_fac·sd_i`, i.e. treated as
  fully correlated), computed in the factory's combine step; all-or-nothing there (a missing
  constituent ensemble errors upstream).
- **float64 end to end** through ingest → derive, so the large-mean anomaly cancellation keeps
  precision in both the values and the `_sd` spread.
