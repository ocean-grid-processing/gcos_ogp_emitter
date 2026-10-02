#!/bin/bash
#SBATCH --qos=preemptable
#SBATCH --export=NONE
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --time=00:30:00
#SBATCH --job-name=ohc_gcos
#SBATCH --output=%x.%j.out
#SBATCH --mail-type=FAIL
#SBATCH --mail-user=katie.mills@colorado.edu
#SBATCH --mem=8GB
#SBATCH --exclude=blanca-g6-u21

module purge
module load anaconda/2023.09
conda activate ohc_crosscheck

# One ohc_derive blob per synthetic level, each built with --time-window 2005:2024 (the GCOS baseline);
# gcos.py reads the window off the blobs. $1 is the provenance tag.
cd $SLURM_SUBMIT_DIR
tag=260921-OP20260507
product_name=LocalGP
author=Giglio_etal2026
citation="Giglio, D., Sukianto, T., Kuusela, M., & Mills, K. (2026). Global Ocean Heat Content Anomalies and Ocean Heat Uptake based on mapping Argo data using local Gaussian processes (Version 4.1.0) [Dataset]. https://doi.org/10.5281/zenodo.22757913"
results=/scratch/alpine/wimi7695/ohc_prod/results_260921
window=$1
# derive names carry the data span too (derive_<tag>_<data>_tw<baseline>_<level>.nc); $window is the
# baseline, so glob the data span in front of it (and the level after).
python gcos.py \
  ${results}/derive_${tag}_*_tw${window}_*.nc \
  --tag ${tag} \
  --provenance-link https://github.com/ocean-grid-processing/provenance/blob/main/README.md \
  --code-version https://github.com/ocean-grid-processing/ohc_gcos_emitter/releases/tag/1.0.0 \
  --product-name ${product_name} --author ${author} --citation "${citation}" \
  --out $results
