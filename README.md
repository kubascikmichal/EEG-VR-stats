# EEG-VR-stats

Analysis of BrainAccess EEG devices used with VR systems, including HTC Vive
and Meta Quest.

## Project structure

- `datafiles/` — raw CSV recordings.
- `processing/` — Jupyter notebooks that process and analyse recordings.
- `outputs/` — generated figures, tables, article content, and TeX build files.

## Input filename convention

Raw recordings use the following pattern:

```text
task_name-session-VR_set-device.csv
```

The dataset covers 8-channel, 16-channel, and 32-channel EEG devices and both
HTC and Meta Quest VR sets. Processing notebooks should use the filename
metadata to keep results traceable to the task, session, VR set, device, and
channel count.

Raw files in `datafiles/` must not be modified. Generated analysis results and
publication files belong in `outputs/`. Each project folder includes a README
describing its purpose.
