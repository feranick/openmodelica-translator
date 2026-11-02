"""render.py — pictures of a finished study as PNG (matplotlib, Agg): time plots and Bode plots.
Version: 2026.10.21.5

save_state(folder, t, values, units)   series.npz: the full output series, for pictures made later
default_images(res)                    'outputs' (one panel per unit) for a run, 'bode' for a linearisation
render(folder, res, specs)             {name: {path, what}}; specs: True (defaults) or
                                       [{name, outputs: [...], window: [t0, t1]} | {name, bode: true}]
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def save_state(folder: Path, t, values: dict, units: dict):
    np.savez_compressed(folder / "series.npz", t=np.asarray(t, float),
                        **{f"y_{k}": np.asarray(v, float) for k, v in values.items()})
    (folder / "series.json").write_text(json.dumps({"outputs": list(values), "units": units}))


def load_state(folder: Path):
    meta = json.loads((folder / "series.json").read_text())
    z = np.load(folder / "series.npz")
    return z["t"], {k: z[f"y_{k}"] for k in meta["outputs"]}, meta["units"]


def default_images(res: dict) -> list[dict]:
    if res.get("linearization", {}).get("bode"):
        return [{"name": "bode", "bode": True}]
    if res.get("outputs"):
        return [{"name": "outputs", "outputs": list(res["outputs"])}]
    return []


def spec_errors(specs, outputs) -> list[str]:
    if specs is True:
        return []
    if not isinstance(specs, list):
        return ["images: true or [{name, outputs: [...], window: [t0, t1]} | {name, bode: true}]"]
    errs = []
    for i, s in enumerate(specs):
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].isidentifier():
            errs.append(f"images[{i}]: {{name (an identifier), outputs or bode}}")
            continue
        for o in s.get("outputs", []):
            if o not in outputs:
                errs.append(f"images[{i}].outputs: '{o}' is not an output ({sorted(outputs)})")
        w = s.get("window")
        if w is not None and not (isinstance(w, list) and len(w) == 2 and w[1] > w[0]):
            errs.append(f"images[{i}].window: [t0, t1] with t1 > t0")
    return errs


def _time_plot(path, t, values, units, names, window=None, title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    groups = {}
    for n in names:
        groups.setdefault(units.get(n, "") or "-", []).append(n)
    fig, axes = plt.subplots(len(groups), 1, figsize=(8, 2.6 * len(groups) + 0.6), sharex=True, squeeze=False)
    m = np.ones(t.size, bool) if window is None else (t >= window[0]) & (t <= window[1])
    for ax, (u, ns) in zip(axes[:, 0], groups.items()):
        for n in ns:
            ax.plot(t[m], values[n][m], label=n, lw=1.2)
        ax.set_ylabel(u)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
    axes[-1, 0].set_xlabel("time [s]")
    if title:
        axes[0, 0].set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _bode_plot(path, bode: dict, title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    for pair, b in bode.items():
        f = np.array(b["f_Hz"], float)
        g = np.array([np.nan if x is None else x for x in b["gain_dB"]], float)
        a1.semilogx(f, g, label=pair, lw=1.2)
        a2.semilogx(f, np.array(b["phase_deg"], float), label=pair, lw=1.2)
    a1.set_ylabel("gain [dB]")
    a2.set_ylabel("phase [deg]")
    a2.set_xlabel("frequency [Hz]")
    for a in (a1, a2):
        a.grid(True, which="both", alpha=0.3)
        a.legend(fontsize=8)
    if title:
        a1.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def render(folder: Path, res: dict, specs=True) -> dict:
    folder = Path(folder)
    img = folder / "images"
    img.mkdir(exist_ok=True)
    specs = default_images(res) if specs is True else specs
    out = {}
    name = res.get("name", folder.name)
    for s in specs:
        p = img / f"{s['name']}.png"
        if s.get("bode"):
            bode = (res.get("linearization") or {}).get("bode")
            if not bode:
                out[s["name"]] = {"error": "no Bode data (linearize with analysis.bode)"}
                continue
            _bode_plot(p, bode, f"{name}: Bode")
            out[s["name"]] = {"path": str(p), "what": "Bode: gain and phase per input -> output"}
        else:
            if not (folder / "series.npz").is_file():
                out[s["name"]] = {"error": "no saved series (a run's folder: <request>/<study>)"}
                continue
            t, values, units = load_state(folder)
            names = s.get("outputs") or list(values)
            _time_plot(p, t, values, units, names, s.get("window"), f"{name}: {', '.join(names)}")
            out[s["name"]] = {"path": str(p), "what": "time series: " + ", ".join(names)}
    return out
