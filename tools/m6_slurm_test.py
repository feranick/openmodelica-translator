"""m6_slurm_test.py — SLURM on the server (docs/EXECUTION.md, "On mochi"): two sweeps submitted by two separate
processes (as from two conversations) each ask for 30 CPUs; on a 40-CPU node the second must queue, then both
finish and are collected by a third process.
Version: 2026.10.21.7
Usage (from the repository, ~1-3 min): OMCT_EXECUTOR=slurm python tools/m6_slurm_test.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUBMIT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from openmodelica_translator import api
ex = json.loads(open(sys.argv[1] + "/examples/studies.json").read())
st = ex["pi_gain_sweep"]
st["sweep"] = {"params": {"pi.k": [0.25 * i for i in range(1, 31)], "pi.T": [0.05, 0.1, 0.2, 0.5]},
               "report": ["derived.y.overshoot_pct", "derived.y.settling_time_s"]}
st["analysis"]["intervals"] = 20000
st["run"] = {"procs": 30, "queue": True}
r = api.run({sys.argv[2]: st}, "omc", wait=0)
print(json.dumps({"request": r["request"], "progress": r.get("progress"), "studies": r.get("studies")}))
'''


def main():
    if os.environ.get("OMCT_EXECUTOR") != "slurm":
        sys.exit("set OMCT_EXECUTOR=slurm")
    from openmodelica_translator import api
    reqs = []
    for name in ("sweep_a", "sweep_b"):
        p = subprocess.run([sys.executable, "-c", SUBMIT, str(ROOT), name], capture_output=True, text=True, timeout=120)
        if p.returncode:
            sys.exit(p.stderr[-2000:])
        d = json.loads(p.stdout.strip().splitlines()[-1])
        reqs.append(d["request"])
        print(name, "submitted:", d["request"], d["progress"] or d["studies"])
    time.sleep(3)
    j = api.jobs(5, active=True)
    print("queue:", j["queue"], "machine:", j["machine"])
    for r in j["requests"]:
        print("  ", Path(r["request"]).name, r["studies"])
    t0 = time.time()
    done = {}
    while len(done) < 2 and time.time() - t0 < 600:
        for req in reqs:
            if req in done:
                continue
            s = api.status(req)
            if s["done"]:
                done[req] = s
                st = next(iter(s["studies"].values()))
                sw = st.get("sweep") or {}
                print(Path(req).name, "done after", round(time.time() - t0, 1), "s: ok", st.get("ok"), "reliable",
                      st.get("reliable"), "points", sw.get("points"), "reliable points", sw.get("reliable_points"),
                      "builds", sw.get("builds"), "sweep s", sw.get("seconds"), "execution", st.get("execution"),
                      st.get("why", ""))
            else:
                print("  ", Path(req).name, {k: {x: v.get(x) for x in ("state", "reason", "stage", "job_id")}
                                             for k, v in s.get("progress", {}).items()})
        time.sleep(5)


if __name__ == "__main__":
    main()
