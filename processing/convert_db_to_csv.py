#!/usr/bin/env python3
"""Convert BrainAccess SQLite recordings (.db) to anonymized CSV files.

Source files (private, git-ignored) live in ``metadata/`` and are named e.g.::

    subj-1_ses-S001_task-impedance_vr_htc_run-001_20261106_125440_eeg.db

Output follows the project convention ``task_name-session-VR_set-device.csv``::

    impedance-S001-HTC-BA_MINI_8ch.csv   (+ .json sidecar with metadata)

Anonymization
-------------
* recording date/time from the filename is dropped;
* device serial number and stream UUID are dropped (only model is kept);
* absolute host clock timestamps are dropped; ``time_s`` is computed as
  sample index / nominal sampling frequency (250 Hz for BA MINI), which is
  valid because the device sample counter is verified to be gap-free;
* the device sample counter is rebased to start at 0;
* the subject code is kept only as a pseudonymous ``subject`` value in the
  sidecar (``sub-<code>``), never the original filename;
* the sidecar stores a SHA-256 of the source file so the CSV stays traceable
  to its private source without revealing it.

Source files are opened read-only and never modified.

Usage::

    python processing/convert_db_to_csv.py --input metadata --output datafiles/impedance
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FILENAME_RE = re.compile(
    r"^subj-(?P<subject>[A-Za-z0-9]+)"
    r"_ses-(?P<session>[A-Za-z0-9]+)"
    r"_task-(?P<task>[a-z0-9]+?)_(?P<vr>no_vr|vr_htc|vr_meta)"
    r"_run-(?P<run>\d+)"
    r"_\d{8}_\d{6}_eeg\.db$"
)
VR_LABELS = {"no_vr": "NoVR", "vr_htc": "HTC", "vr_meta": "MetaQuest"}
DEVICE_RE = re.compile(r"^(?P<model>BA_[A-Z]+)_\d+_[0-9a-f-]{36}$")
SAMPLE_CHANNEL = "Sample"


class ConversionError(RuntimeError):
    pass


def parse_filename(path: Path) -> dict:
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ConversionError(f"{path.name}: filename does not match expected pattern")
    d = m.groupdict()
    d["vr_set"] = VR_LABELS[d.pop("vr")]
    return d


def read_db(path: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [n for (n,) in con.execute("select name from sqlite_master where type='table'")]
        data_t = [t for t in tables if t.startswith("data_")]
        meta_t = [t for t in tables if t.startswith("meta_")]
        if len(data_t) != 1 or len(meta_t) != 1:
            raise ConversionError(f"{path.name}: expected one data_ and one meta_ table, got {tables}")
        meta_rows = con.execute(f'select channels, channels_type, channels_unit, sf, id from "{meta_t[0]}"').fetchall()
        if len(meta_rows) != 1:
            raise ConversionError(f"{path.name}: expected 1 meta row, got {len(meta_rows)}")
        ch, ch_type, ch_unit, sf, dev_id = meta_rows[0]
        meta = {
            "channels": ch.split(","),
            "channel_types": ch_type.split(","),
            "channel_units": ch_unit.split(","),
            "sf": float(sf),
            "device_id": dev_id,
        }
        chunks, times = [], []
        for d, t in con.execute(f'select data, time from "{data_t[0]}" order by local_clock, rowid'):
            chunks.append(np.load(io.BytesIO(d), allow_pickle=False))
            times.append(np.load(io.BytesIO(t), allow_pickle=False))
    finally:
        con.close()
    if not chunks:
        raise ConversionError(f"{path.name}: no data rows")
    return meta, np.concatenate(chunks, axis=1), np.concatenate(times)


def validate(name: str, meta: dict, data: np.ndarray, t: np.ndarray) -> None:
    n_ch = len(meta["channels"])
    if not (len(meta["channel_types"]) == len(meta["channel_units"]) == n_ch):
        raise ConversionError(f"{name}: channel/type/unit list lengths differ")
    if data.shape != (n_ch, t.size):
        raise ConversionError(f"{name}: data shape {data.shape} does not match {n_ch} channels x {t.size} timestamps")
    if np.isnan(data).any() or np.isnan(t).any():
        raise ConversionError(f"{name}: NaN values present")
    if not (np.diff(t) > 0).all():
        raise ConversionError(f"{name}: timestamps not strictly increasing")
    if meta["sf"] <= 0:
        raise ConversionError(f"{name}: invalid sampling frequency {meta['sf']}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def convert(path: Path, out_dir: Path, overwrite: bool) -> Path:
    info = parse_filename(path)
    meta, data, t = read_db(path)
    validate(path.name, meta, data, t)

    m = DEVICE_RE.match(meta["device_id"])
    if not m:
        raise ConversionError(f"{path.name}: unrecognized device id format")
    model = m["model"]
    n_eeg = meta["channel_types"].count("EEG")
    device = f"{model}_{n_eeg}ch"

    stem = f"{info['task']}-{info['session']}-{info['vr_set']}-{device}"
    csv_path, json_path = out_dir / f"{stem}.csv", out_dir / f"{stem}.json"
    if not overwrite and (csv_path.exists() or json_path.exists()):
        raise ConversionError(f"{csv_path} exists; use --overwrite")

    df = pd.DataFrame(data.T, columns=meta["channels"])
    sample_info = {}
    if SAMPLE_CHANNEL in df:
        s = df[SAMPLE_CHANNEL].to_numpy()
        gaps = np.diff(s)
        sample_info = {
            "sample_counter_continuous": bool((gaps == 1).all()),
            "n_sample_counter_gaps": int((gaps != 1).sum()),
        }
        df[SAMPLE_CHANNEL] = (s - s[0]).astype(np.int64)
    if not sample_info.get("sample_counter_continuous", False):
        raise ConversionError(f"{path.name}: sample counter has gaps; time axis from fs would be wrong")
    fs = meta["sf"]
    df.insert(0, "time_s", np.arange(t.size) / fs)

    duration = t.size / fs
    sidecar = {
        "file": csv_path.name,
        "subject": f"sub-{info['subject']}",
        "task": info["task"],
        "session": info["session"],
        "run": info["run"],
        "vr_set": info["vr_set"],
        "device": device,
        "device_model": model,
        "n_eeg_channels": n_eeg,
        "channels": {c: {"type": ty, "unit": u}
                     for c, ty, u in zip(meta["channels"], meta["channel_types"], meta["channel_units"])},
        "time_column": {"name": "time_s", "unit": "s", "definition": "sample index / sampling_frequency_hz"},
        "sampling_frequency_hz": fs,
        "n_samples": int(t.size),
        "duration_s": round(duration, 3),
        **sample_info,
        "source_sha256": sha256(path),
        "converted_by": "processing/convert_db_to_csv.py",
        "converted_on": dt.date.today().isoformat(),
        "anonymization": [
            "recording date/time removed",
            "device serial and stream UUID removed",
            "absolute host timestamps removed (time_s derived from sample index and fs)",
            "sample counter rebased to 0",
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, float_format="%.10g")
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n")
    return csv_path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=root / "metadata")
    ap.add_argument("--output", type=Path, default=root / "datafiles" / "impedance")
    ap.add_argument("--pattern", default="*_eeg.db")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    sources = sorted(args.input.glob(args.pattern))
    if not sources:
        print(f"No files matching {args.pattern} in {args.input}", file=sys.stderr)
        return 1
    # Detect output-name collisions before writing anything.
    stems = {}
    for p in sources:
        i = parse_filename(p)
        stems.setdefault((i["task"], i["session"], i["vr_set"], i["subject"], i["run"]), []).append(p.name)
    keys = {(k[0], k[1], k[2]) for k in stems}
    if len(keys) != len(stems):
        raise ConversionError("Multiple subjects/runs map to the same output filename; "
                              "the task-session-VR_set-device pattern cannot distinguish them")

    for p in sources:
        out = convert(p, args.output, args.overwrite)
        print(f"{p.name} -> {out.relative_to(root) if out.is_relative_to(root) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
