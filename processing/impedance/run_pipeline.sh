#!/usr/bin/env bash
# Impedance pipeline: metadata/*.db -> datafiles/impedance/*.csv -> notebook -> report PDF.
#
#   processing/impedance/run_pipeline.sh            # uses Jupyter kernel "python3"
#   KERNEL=dp-venv processing/impedance/run_pipeline.sh
#
# Steps
#   1. convert all impedance recordings in metadata/ (recursive) to anonymized CSV,
#      replacing previous CSVs (--clean); raw files are never modified
#   2. delete previously generated figures/tables/macros in outputs/impedance
#      (the hand-written report .tex in outputs/impedance/article is kept)
#   3. execute processing/impedance/impedance_over_time.ipynb in place
#   4. rebuild outputs/impedance/article/impedance_report.pdf
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
KERNEL="${KERNEL:-python3}"
PY="${PYTHON:-python3}"

echo "== 1/4 convert metadata -> datafiles/impedance"
"$PY" processing/convert_db_to_csv.py --task impedance --clean

echo "== 2/4 remove previous generated outputs"
find outputs/impedance -type f \( -path 'outputs/impedance/figures/*' -o -path 'outputs/impedance/tables/*' \
     -o -name 'impedance_results_*.tex' -o -name 'impedance_provenance_*.json' \) -print -delete

echo "== 3/4 run notebook (kernel: $KERNEL)"
"$PY" processing/run_notebook.py processing/impedance/impedance_over_time.ipynb --kernel "$KERNEL"

echo "== 4/4 build report"
cd outputs/impedance/article
latexmk -pdf -interaction=nonstopmode -halt-on-error impedance_report.tex > /dev/null || {
    echo "LaTeX failed; see outputs/impedance/article/impedance_report.log" >&2
    grep -E '^!' -A3 impedance_report.log >&2 || true
    exit 1
}
if grep -qE 'Overfull|Undefined|undefined' impedance_report.log; then
    echo "LaTeX warnings:" >&2
    grep -E 'Overfull|Undefined|undefined' impedance_report.log >&2
fi
echo "Report: outputs/impedance/article/impedance_report.pdf ($(pdfinfo impedance_report.pdf | awk '/Pages/ {print $2}') pages)"
