# Data files

This folder contains the raw EEG recording CSV files used by the analysis.

Files use the following naming pattern:

```text
task_name-session-VR_set-device.csv
```

The recordings cover 8-channel, 16-channel, and 32-channel EEG devices used
with HTC and Meta Quest VR sets. Raw files must not be modified; processing
notebooks should read them as inputs and preserve their metadata in results.
