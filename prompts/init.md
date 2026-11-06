# Claude project prompt

You are working on the EEG-VR-stats project. The project processes EEG recordings
collected during virtual-reality experiments and produces reproducible analysis
results and publication-ready documents.

## Repository layout

- `datafiles/` contains the raw input CSV files. Never overwrite or modify these
  files.
- `processing/` contains the Jupyter notebooks used to process and analyse the
  data.
- `outputs/` contains generated results, including article files and TeX files
  used to build the final documents.
- `prompts/` contains project instructions.

## Input files

Each raw CSV file follows this naming pattern:

```text
task_name-session-VR_set-device.csv
```

The fields are:

- `task_name`: the experimental task;
- `session`: the recording session identifier;
- `VR_set`: the VR platform, currently HTC or Meta Quest;
- `device`: the EEG device and its channel configuration.

The project includes recordings from 8-channel, 16-channel, and 32-channel EEG
devices, used with both HTC and Meta Quest VR sets. Treat the filename as
metadata, validate it before processing, and preserve the original values in
analysis outputs.

## Processing requirements

When adding or changing analysis:

1. Inspect the available files and determine the task, session, VR set, device,
   and channel count from the filename and data columns.
2. Keep notebooks in `processing/`, with one clear entry point per task or
   analysis workflow. Avoid hard-coded paths to a single recording.
3. Make notebooks reusable across all supported combinations:
   - 8, 16, and 32 EEG channels;
   - HTC and Meta Quest VR sets;
   - every task and session present in `datafiles/`.
4. Use configurable input and output paths, and make the selected file,
   parameters, and processing date visible in the notebook output.
5. Validate required columns, sampling information, channel counts, and missing
   values before calculating statistics. Fail with a clear error when input is
   invalid; do not silently substitute values.
6. Keep intermediate and final results traceable to the source filename.
   Include task, session, VR set, device, and channel count in result names or
   metadata.
7. Write generated figures, tables, article content, and TeX build sources to
   `outputs/`. Use deterministic names and do not mix generated files with raw
   data or notebooks.
8. Do not commit credentials, private data, temporary notebook checkpoints, or
   large derived files unless the repository explicitly requires them.

## Scientific and documentation standards

- Explain assumptions, preprocessing steps, exclusions, and statistical tests
  in notebook Markdown cells.
- Prefer reproducible Python code and explicit parameters over manual edits.
- Label plots and tables with units, sample sizes, and the relevant experimental
  conditions.
- When producing an article or TeX output, ensure that its claims and numbers
  are generated from the same processed data and identify the source notebook.
- Preserve existing project conventions. Before introducing a dependency, check
  whether an existing library or helper already provides the required behavior.
- After making changes, run the smallest relevant validation and report what was
  checked, which files were generated, and any limitations.