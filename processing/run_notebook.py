#!/usr/bin/env python3
"""Execute a Jupyter notebook in place and store its outputs.

Minimal replacement for ``jupyter nbconvert --execute`` / nbclient using only
``jupyter_client`` and ``nbformat``. Cells run in order in the notebook's own
directory; execution stops at the first error and the script exits non-zero.

Usage::

    python processing/run_notebook.py processing/impedance/impedance_over_time.ipynb
    python processing/run_notebook.py NOTEBOOK --kernel dp-venv --timeout 900
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from queue import Empty

import nbformat as nbf
from jupyter_client.manager import start_new_kernel

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(path: Path, kernel: str, timeout: int) -> bool:
    nb = nbf.read(path, as_version=4)
    km, kc = start_new_kernel(kernel_name=kernel, cwd=str(path.parent))
    ok = True
    try:
        kc.execute("%matplotlib inline", silent=True)
        kc.get_shell_msg(timeout=60)
        count = 0
        for cell in nb.cells:
            if cell.cell_type != "code":
                continue
            count += 1
            msg_id = kc.execute(cell.source)
            outputs = []
            while True:
                try:
                    msg = kc.get_iopub_msg(timeout=timeout)
                except Empty:
                    raise RuntimeError(f"cell {count}: no output for {timeout} s")
                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                t, c = msg["msg_type"], msg["content"]
                if t == "status" and c["execution_state"] == "idle":
                    break
                if t == "stream":
                    if outputs and outputs[-1].get("output_type") == "stream" and outputs[-1]["name"] == c["name"]:
                        outputs[-1]["text"] += c["text"]
                    else:
                        outputs.append(nbf.v4.new_output("stream", name=c["name"], text=c["text"]))
                elif t in ("display_data", "execute_result"):
                    o = nbf.v4.new_output(t, data=c["data"], metadata=c.get("metadata", {}))
                    if t == "execute_result":
                        o["execution_count"] = count
                    outputs.append(o)
                elif t == "error":
                    outputs.append(nbf.v4.new_output("error", ename=c["ename"], evalue=c["evalue"],
                                                     traceback=c["traceback"]))
                    ok = False
            cell.outputs, cell.execution_count = outputs, count
            if not ok:
                tb = "\n".join("\n".join(o["traceback"]) for o in outputs if o.get("output_type") == "error")
                print(f"ERROR in code cell {count}:\n{ANSI.sub('', tb)}", file=sys.stderr)
                break
    finally:
        kc.stop_channels()
        km.shutdown_kernel(now=True)
    nbf.write(nb, path)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notebook", type=Path)
    ap.add_argument("--kernel", default="python3", help="Jupyter kernel name (see `jupyter kernelspec list`)")
    ap.add_argument("--timeout", type=int, default=600, help="seconds without output before giving up")
    args = ap.parse_args()
    path = args.notebook.resolve()
    ok = run(path, args.kernel, args.timeout)
    print(("OK " if ok else "FAILED ") + str(path))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
