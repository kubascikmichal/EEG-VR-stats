#!/usr/bin/env python3
"""Convert BrainAccess SQLite recordings (.db) to anonymized CSV files.

Source files (private, git-ignored) live in ``metadata/``, optionally in one
sub-folder per device model (``metadata/MINI/``, ``metadata/MIDI/``), and are
named e.g.::

    subj-1_ses-S001_task-impedance_vr_htc_run-001_20261106_125440_eeg.db
    subj-1_ses-S001_task-impedance_htc_run-001_20261106_154457_eeg.db

Output follows the project convention ``task_name-session-VR_set-device.csv``::

    impedance-S001-HTC-BA_MINI_8ch.csv   (+ .json sidecar with metadata)

Only recordings of the requested ``--task`` are converted; other tasks found
in ``metadata/`` (e.g. ECG) are listed as skipped.

SQLite write-ahead log
----------------------
The acquisition software writes in WAL mode, so the newest data may still be
in ``<file>.db-wal``. Each ``.db`` and its ``-wal`` are copied to a temporary
directory and read from there, so the WAL content is included and the source
files (including ``-shm``) are never touched.

Anonymization
-------------
* recording date/time from the filename is dropped; only the order of the
  recordings within subject x session x task x device and their start offset
  in whole minutes relative to the first recording are kept;
* device serial number and stream UUID are dropped (only model is kept);
* absolute host clock timestamps are dropped; ``time_s`` is computed as
  sample index / nominal sampling frequency, which is valid because the device
  sample counter is verified to be gap-free;
* the device sample counter is rebased to start at 0;
* the subject code is kept only as a pseudonymous ``subject`` value in the
  sidecar (``sub-<code>``), never the original filename;
* the sidecar stores a SHA-256 of the source (.db followed by -wal) so the CSV
  stays traceable to its private source without revealing it.

Usage::

    python processing/convert_db_to_csv.py                      # defaults below
    python processing/convert_db_to_csv.py --input metadata --output datafiles/impedance \\
        --task impedance --clean
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

FILENAME_RE = re.compile(
    r"^subj-(?P<subject>[A-Za-z0-9]+)"
    r"_ses-(?P<session>[A-Za-z0-9]+)"
    r"_task-(?P<task>[a-z0-9]+?)(?:_(?P<vr>no_vr|vr_htc|vr_meta|htc|meta))?"
    r"_run-(?P<run>\d+)"
    r"_(?P<date>\d{8})_(?P<time>\d{6})_eeg\.db$"
)
VR_LABELS = {"no_vr": "NoVR", "vr_htc": "HTC", "htc": "HTC", "vr_meta": "MetaQuest", "meta": "MetaQuest"}
DEVICE_RE = re.compile(r"^(?P<model>BA_(?P<short>[A-Z]+))_\d+_[0-9a-f-]{36}$")
SAMPLE_CHANNEL = "Sample"


class ConversionError(RuntimeError):
    pass


def parse_filename(path: Path) -> dict:
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ConversionError(f"{path}: filename does not match expected pattern")
    d = m.groupdict()
    d["started"] = dt.datetime.strptime(d.pop("date") + d.pop("time"), "%Y%m%d%H%M%S")
    vr = d.pop("vr")
    d["vr_set"] = VR_LABELS[vr] if vr else None
    return d


def read_db(path: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    """Read a copy of the database (+ WAL) so the source is never modified."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / path.name
        shutil.copy2(path, copy)
        wal = path.with_name(path.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, copy.with_name(copy.name + "-wal"))
        con = sqlite3.connect(copy)
        try:
            tables = [n for (n,) in con.execute("select name from sqlite_master where type='table'")]
            data_t = [t for t in tables if t.startswith("data_")]
            meta_t = [t for t in tables if t.startswith("meta_")]
            if len(data_t) != 1 or len(meta_t) != 1:
                raise ConversionError(f"{path.name}: expected one data_ and one meta_ table, got {tables}")
            meta_rows = con.execute(
                f'select channels, channels_type, channels_unit, sf, id from "{meta_t[0]}"').fetchall()
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
    if SAMPLE_CHANNEL not in meta["channels"]:
        raise ConversionError(f"{name}: no {SAMPLE_CHANNEL} channel; cannot verify continuity")
    s = data[meta["channels"].index(SAMPLE_CHANNEL)]
    gaps = int((np.diff(s) != 1).sum())
    if gaps:
        raise ConversionError(f"{name}: sample counter has {gaps} gaps; time axis from fs would be wrong")


def sha256(*paths: Path) -> str:
    h = hashlib.sha256()
    for path in paths:
        if path.exists():
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
    return h.hexdigest()


def load(path: Path, input_root: Path) -> dict:
    info = parse_filename(path)
    meta, data, t = read_db(path)
    validate(path.name, meta, data, t)
    m = DEVICE_RE.match(meta["device_id"])
    if not m:
        raise ConversionError(f"{path.name}: unrecognized device id format")
    folder = path.parent.relative_to(input_root)
    if folder.parts and folder.parts[0].upper() != m["short"]:
        raise ConversionError(f"{path}: stored in folder '{folder.parts[0]}' but device is {m['model']}")
    n_eeg = meta["channel_types"].count("EEG")
    if n_eeg == 0:
        raise ConversionError(f"{path.name}: no EEG channels")
    return dict(path=path, info=info, meta=meta, data=data, t=t,
                model=m["model"], device=f"{m['model']}_{n_eeg}ch", n_eeg=n_eeg)


def write(rec: dict, out_dir: Path) -> Path:
    info, meta, data, t = rec["info"], rec["meta"], rec["data"], rec["t"]
    stem = f"{info['task']}-{info['session']}-{info['vr_set']}-{rec['device']}"
    csv_path, json_path = out_dir / f"{stem}.csv", out_dir / f"{stem}.json"

    df = pd.DataFrame(data.T, columns=meta["channels"])
    s = df[SAMPLE_CHANNEL].to_numpy()
    df[SAMPLE_CHANNEL] = (s - s[0]).astype(np.int64)
    fs = meta["sf"]
    df.insert(0, "time_s", np.arange(t.size) / fs)

    src = rec["path"]
    sidecar = {
        "file": csv_path.name,
        "subject": f"sub-{info['subject']}",
        "task": info["task"],
        "session": info["session"],
        "run": info["run"],
        "vr_set": info["vr_set"],
        "device": rec["device"],
        "device_model": rec["model"],
        "n_eeg_channels": rec["n_eeg"],
        "recording_order": rec["order"],
        "start_offset_min": rec["offset_min"],
        "recording_order_scope": "subject x session x task x device",
        "channels": {c: {"type": ty, "unit": u}
                     for c, ty, u in zip(meta["channels"], meta["channel_types"], meta["channel_units"])},
        "time_column": {"name": "time_s", "unit": "s", "definition": "sample index / sampling_frequency_hz"},
        "sampling_frequency_hz": fs,
        "n_samples": int(t.size),
        "duration_s": round(t.size / fs, 3),
        "sample_counter_continuous": True,
        "n_sample_counter_gaps": 0,
        "source_sha256": sha256(src, src.with_name(src.name + "-wal")),
        "source_sha256_scope": ".db followed by .db-wal (if present)",
        "converted_by": "processing/convert_db_to_csv.py",
        "converted_on": dt.date.today().isoformat(),
        "anonymization": [
            "recording date/time removed (only order and start offset in whole minutes kept)",
            "device serial and stream UUID removed",
            "absolute host timestamps removed (time_s derived from sample index and fs)",
            "sample counter rebased to 0",
        ],
    }
    df.to_csv(csv_path, index=False, float_format="%.10g")
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n")
    return csv_path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=root / "metadata", help="searched recursively")
    ap.add_argument("--output", type=Path, default=root / "datafiles" / "impedance")
    ap.add_argument("--task", default="impedance", help="only convert this task")
    ap.add_argument("--pattern", default="*_eeg.db")
    ap.add_argument("--overwrite", action="store_true", help="replace existing output files")
    ap.add_argument("--clean", action="store_true",
                    help="delete existing <task>-*.csv/.json in the output folder first (implies --overwrite)")
    args = ap.parse_args(argv)
    input_root = args.input.resolve()

    sources = sorted(input_root.rglob(args.pattern))
    if not sources:
        print(f"No files matching {args.pattern} under {input_root}", file=sys.stderr)
        return 1

    selected = []
    for p in sources:
        info = parse_filename(p)
        if info["task"] != args.task:
            print(f"skip (task '{info['task']}'): {p.relative_to(input_root)}")
            continue
        if info["vr_set"] is None:
            raise ConversionError(f"{p}: task '{args.task}' but no VR condition in filename")
        selected.append(p)
    if not selected:
        print(f"No '{args.task}' recordings found", file=sys.stderr)
        return 1

    # Load and validate everything before writing anything.
    recs = [load(p, input_root) for p in selected]

    seen = {}
    for r in recs:
        key = (r["info"]["task"], r["info"]["session"], r["info"]["vr_set"], r["device"])
        if key in seen:
            raise ConversionError(f"{r['path'].name} and {seen[key]} map to the same output name {key}; "
                                  "the task-session-VR_set-device pattern cannot distinguish them")
        seen[key] = r["path"].name

    groups = {}
    for r in recs:
        i = r["info"]
        groups.setdefault((i["subject"], i["session"], i["task"], r["device"]), []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: r["info"]["started"])
        t0 = g[0]["info"]["started"]
        for k, r in enumerate(g, start=1):
            r["order"] = k
            r["offset_min"] = int(round((r["info"]["started"] - t0).total_seconds() / 60))

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for old in sorted(out_dir.glob(f"{args.task}-*.csv")) + sorted(out_dir.glob(f"{args.task}-*.json")):
            old.unlink()
            print(f"removed {old.relative_to(root) if old.is_relative_to(root) else old}")
    elif not args.overwrite:
        for key in seen:
            stem = "-".join(key)
            if (out_dir / f"{stem}.csv").exists():
                raise ConversionError(f"{stem}.csv exists; use --overwrite or --clean")

    for r in recs:
        out = write(r, out_dir)
        print(f"{r['path'].relative_to(input_root)} -> {out.relative_to(root) if out.is_relative_to(root) else out}"
              f"  ({r['data'].shape[1]} samples, order {r['order']}, +{r['offset_min']} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
