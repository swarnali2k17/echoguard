"""Extract features from dataset.npz and evaluate.

Outputs (all under this directory):
  features_48k.csv / features_16k.csv   per-clip features + metadata
  per_feature_auc.csv                   AUC attack-vs-benign per feature / rate
  feature_importance.csv                LR |coef| (standardised) and GB importances
  results.json                          CV AUC / EER / look-alike FA / robustness
  fig_psd_0_300.png, fig_modspec.png, fig_feature_auc.png
"""
from __future__ import annotations

import csv
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal as sps
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score, roc_curve

import features as F

HERE = os.path.dirname(os.path.abspath(__file__))
# absolute-level features are excluded from the models: in the fork chain the carrier is
# normalised to full scale, so attack baseband is ~20 dB quieter than benign content -- a
# gain/AGC artefact, not a cue.  They stay in the per-feature AUC table as flagged confounds.
LEVEL_FEATURES = {"a_lf2_20_dbfs"}
MODEL_IDX = [i for i, n in enumerate(F.FEATURE_NAMES) if n not in LEVEL_FEATURES]
MODEL_NAMES = [F.FEATURE_NAMES[i] for i in MODEL_IDX]
RATES = {48_000: "x48", 16_000: "x16"}


def load():
    d = np.load(os.path.join(HERE, "dataset.npz"))
    keys = list(d["meta_keys"])
    meta = [dict(zip(keys, row)) for row in d["meta"]]
    for m in meta:
        for k in ("label",):
            m[k] = int(m[k])
        for k in ("carrier_hz", "mod_depth", "room_level", "snr_db", "distance_m",
                  "mic_a2", "mic_a3", "rt60", "drr_db"):
            m[k] = float(m[k])
    return {fs: d[k].astype(np.float64) / 32767.0 for fs, k in RATES.items()}, meta


def _ext(args):
    x, fs = args
    return F.extract(x, fs)


def extract_all(X, meta):
    feats = {}
    if os.path.exists(os.path.join(HERE, "features_48k.csv")) and "--reuse" in sys.argv:
        for fs in RATES:
            rows = list(csv.DictReader(open(os.path.join(HERE, f"features_{fs // 1000}k.csv"))))
            feats[fs] = np.array([[float(r[n]) for n in F.FEATURE_NAMES] for r in rows])
        return feats
    for fs in RATES:
        path = os.path.join(HERE, f"features_{fs // 1000}k.csv")
        with ProcessPoolExecutor() as ex:
            rows = list(ex.map(_ext, [(x, fs) for x in X[fs]], chunksize=8))
        feats[fs] = np.array(rows)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(list(meta[0].keys()) + F.FEATURE_NAMES)
            for m, r in zip(meta, rows):
                w.writerow(list(m.values()) + list(r))
        print("features", fs, feats[fs].shape)
    return feats


def eer(y, s):
    fpr, tpr, _ = roc_curve(y, s)
    fnr = 1 - tpr
    i = np.nanargmin(np.abs(fnr - fpr))
    return float((fpr[i] + fnr[i]) / 2)


def models():
    return {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
        "gboost": HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05,
                                                 max_depth=3, random_state=0),
    }


def cv_eval(Xf, y, name, seed=0):
    skf = StratifiedKFold(5, shuffle=True, random_state=seed)
    oof = np.zeros(len(y))
    for tr, te in skf.split(Xf, y):
        mdl = models()[name].fit(Xf[tr], y[tr])
        oof[te] = mdl.predict_proba(Xf[te])[:, 1]
    return oof


def thr_at_tpr(y, s, tpr_target=0.95):
    sa = np.sort(s[y == 1])
    return float(sa[int(np.floor((1 - tpr_target) * len(sa)))])


def main():
    X, meta = load()
    feats = extract_all(X, meta)

    cls = np.array([m["cls"] for m in meta])
    y = np.array([m["label"] for m in meta])
    kinds_all = np.array([m["kind"] for m in meta])
    with_tone = "--with-tone" in sys.argv
    main_m = (cls != "lookalike") & (with_tone | (kinds_all != "tone_hf"))
    look_m = cls == "lookalike"
    tag = "_tone" if with_tone else ""
    res = {"n_attack": int((cls == "attack").sum()), "n_benign": int((cls == "benign").sum()),
           "n_lookalike": int(look_m.sum())}

    # ---------------- per-feature AUC
    auc_rows = []
    for i, n in enumerate(F.FEATURE_NAMES):
        row = {"feature": n}
        for fs in RATES:
            a = roc_auc_score(y[main_m], feats[fs][main_m, i])
            row[f"auc_{fs // 1000}k"] = round(float(a), 3)
            # also split by modulator type (env vs real speech)
            for mod in ("env", "speech"):
                sel = main_m & ((cls == "benign") | (np.array([m["modulator"] for m in meta]) == mod))
                row[f"auc_{fs // 1000}k_{mod}"] = round(float(roc_auc_score(y[sel], feats[fs][sel, i])), 3)
        auc_rows.append(row)
    with open(os.path.join(HERE, f"per_feature_auc{tag}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(auc_rows[0].keys()))
        w.writeheader(); w.writerows(auc_rows)
    res["per_feature_auc"] = auc_rows

    # ---------------- models, CV, EER, look-alike FA
    imp_rows = []
    feats = {fs: feats[fs][:, MODEL_IDX] for fs in RATES}
    for fs in RATES:
        Xf = feats[fs]
        for name in ("logreg", "gboost"):
            oof = cv_eval(Xf[main_m], y[main_m], name)
            auc = float(roc_auc_score(y[main_m], oof))
            e = eer(y[main_m], oof)
            thr = thr_at_tpr(y[main_m], oof, 0.95)
            fa_benign = float((oof[y[main_m] == 0] >= thr).mean())
            full = models()[name].fit(Xf[main_m], y[main_m])
            ls = full.predict_proba(Xf[look_m])[:, 1]
            # note: threshold from OOF scores, applied to a full-data model (slightly optimistic)
            fa_look = {}
            lk = np.array([m["kind"] for m in meta])[look_m]
            for kind in np.unique(lk):
                fa_look[kind] = float((ls[lk == kind] >= thr).mean())
            fa_look["all"] = float((ls >= thr).mean())
            res[f"{name}_{fs // 1000}k"] = dict(cv_auc=round(auc, 4), eer=round(e, 4),
                                                 thr95=round(thr, 4), fa_benign_at_95=round(fa_benign, 4),
                                                 fa_lookalike_at_95=fa_look)
            print(fs, name, res[f"{name}_{fs // 1000}k"])
            if name == "logreg":
                coef = full.named_steps["logisticregression"].coef_[0]
                for n, c in zip(MODEL_NAMES, coef):
                    imp_rows.append({"rate": fs, "model": name, "feature": n, "importance": round(float(c), 4)})
            else:
                from sklearn.inspection import permutation_importance
                pi = permutation_importance(full, Xf[main_m], y[main_m], scoring="roc_auc",
                                            n_repeats=5, random_state=0)
                for n, c in zip(MODEL_NAMES, pi.importances_mean):
                    imp_rows.append({"rate": fs, "model": name, "feature": n, "importance": round(float(c), 4)})
    with open(os.path.join(HERE, f"feature_importance{tag}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rate", "model", "feature", "importance"])
        w.writeheader(); w.writerows(imp_rows)

    # ---------------- robustness splits
    car = np.array([m["carrier_hz"] for m in meta])
    a2 = np.array([m["mic_a2"] for m in meta])
    benign = main_m & (cls == "benign")
    attack = cls == "attack"
    rng = np.random.default_rng(0)
    # split benign at random into two halves so train/test benign are disjoint
    bh = rng.uniform(size=len(meta)) < 0.5
    splits = {
        "train_hi(25-40k)_test_near(18-22k)": ((attack & (car >= 25000)) | (benign & bh),
                                               (attack & (car < 22500)) | (benign & ~bh)),
        "train_near(18-22k)_test_hi(25-40k)": ((attack & (car < 22500)) | (benign & bh),
                                               (attack & (car >= 25000)) | (benign & ~bh)),
        "train_a2=0.12_test_a2=0.03": ((main_m & np.isclose(a2, 0.12)), (main_m & np.isclose(a2, 0.03))),
        "train_a2>=0.12_test_a2<=0.05": ((main_m & (a2 >= 0.115)), (main_m & (a2 <= 0.055))),
    }
    res["robustness"] = {}
    for sname, (tr, te) in splits.items():
        res["robustness"][sname] = {}
        for fs in RATES:
            for name in ("logreg", "gboost"):
                mdl = models()[name].fit(feats[fs][tr], y[tr])
                s = mdl.predict_proba(feats[fs][te])[:, 1]
                res["robustness"][sname][f"{name}_{fs // 1000}k"] = dict(
                    auc=round(float(roc_auc_score(y[te], s)), 4), eer=round(eer(y[te], s), 4),
                    n_train=int(tr.sum()), n_test=int(te.sum()))
        print(sname, res["robustness"][sname])

    # ---------------- per-condition detection at the 95%-TPR threshold (gboost 16k & 48k)
    res["per_subgroup_tpr"] = {}
    for fs in RATES:
        oof = cv_eval(feats[fs][main_m], y[main_m], "gboost")
        thr = thr_at_tpr(y[main_m], oof, 0.95)
        mm = [m for m, k in zip(meta, main_m) if k]
        sub = {}
        for key in ("scheme", "modulator", "kind"):
            vals = np.array([m[key] for m in mm])
            for v in np.unique(vals):
                sel = vals == v
                lab = y[main_m][sel]
                if lab.sum() > 0:
                    sub[f"{key}={v}_tpr"] = round(float((oof[sel][lab == 1] >= thr).mean()), 3)
                if (lab == 0).sum() > 0:
                    sub[f"{key}={v}_fpr"] = round(float((oof[sel][lab == 0] >= thr).mean()), 3)
        cc = np.array([m["carrier_hz"] for m in mm])
        for lo, hi in ((18000, 22000), (25000, 32000), (32000, 40001)):
            sel = (cc >= lo) & (cc < hi)
            sub[f"carrier_{lo // 1000}-{hi // 1000}k_tpr"] = round(float((oof[sel] >= thr).mean()), 3)
        aa = np.array([m["mic_a2"] for m in mm])
        for v in sorted(set(aa)):
            sel = (aa == v) & (y[main_m] == 1)
            sub[f"a2={v}_tpr"] = round(float((oof[sel] >= thr).mean()), 3)
        res["per_subgroup_tpr"][f"gboost_{fs // 1000}k"] = sub

    with open(os.path.join(HERE, f"results{tag}.json"), "w") as f:
        json.dump(res, f, indent=1)

    # ---------------- figures
    if not with_tone:
        make_figures(X, meta, feats, auc_rows)


def make_figures(X, meta, feats, auc_rows):
    cls = np.array([m["cls"] for m in meta])
    mod = np.array([m["modulator"] for m in meta])
    kind = np.array([m["kind"] for m in meta])
    fs = 48_000
    x = X[fs]

    def mean_psd(sel, nper=8192):
        ps = []
        for xi in x[sel]:
            f, p = sps.welch(xi - xi.mean(), fs=fs, nperseg=nper)
            ps.append(p / (np.trapezoid(p, f) + 1e-20))   # normalise to unit total power
        return f, 10 * np.log10(np.mean(ps, axis=0) + 1e-20)

    plt.figure(figsize=(8, 4.5))
    for sel, lab in (((cls == "attack") & (mod == "env"), "attack, envelope modulator (fork model)"),
                     ((cls == "attack") & (mod == "speech"), "attack, real-speech modulator"),
                     ((cls == "benign") & (kind == "real_speech"), "benign real speech (VCTK)"),
                     ((cls == "benign") & (kind == "music"), "benign music"),
                     ((cls == "benign") & (kind == "silence"), "benign room tone"),
                     ((cls == "lookalike") & (kind == "psu_whine"), "look-alike PSU whine")):
        f, p = mean_psd(sel)
        m = f <= 300
        plt.plot(f[m], p[m], label=lab)
    plt.xlabel("Hz"); plt.ylabel("mean normalised PSD (dB)"); plt.title("PSD 0-300 Hz after 48 kHz capture")
    plt.legend(fontsize=8); plt.grid(alpha=.3); plt.tight_layout()
    plt.savefig(os.path.join(HERE, "fig_psd_0_300.png"), dpi=130); plt.close()

    # modulation spectrum of the 300-3400 Hz envelope
    def modspec(sel):
        out = []
        for xi in x[sel]:
            sos = sps.butter(4, [300.0, 3400.0], btype="bandpass", fs=fs, output="sos")
            env = np.abs(sps.hilbert(sps.sosfiltfilt(sos, xi)))
            env = sps.sosfiltfilt(sps.butter(2, 50.0, btype="low", fs=fs, output="sos"), env)
            e = env[int(.05 * fs):-int(.05 * fs):fs // 200]
            e = e - e.mean()
            fe, pe = sps.welch(e, fs=200, nperseg=128)
            out.append(pe / (np.trapezoid(pe, fe) + 1e-20))
        return fe, 10 * np.log10(np.mean(out, axis=0) + 1e-20)

    plt.figure(figsize=(8, 4.5))
    for sel, lab in (((cls == "attack") & (mod == "env"), "attack, envelope modulator"),
                     ((cls == "attack") & (mod == "speech"), "attack, real-speech modulator"),
                     ((cls == "benign") & (kind == "real_speech"), "benign real speech"),
                     ((cls == "benign") & (kind == "music"), "benign music"),
                     ((cls == "lookalike") & (kind == "percussive"), "look-alike percussive")):
        fe, pe = modspec(sel)
        plt.plot(fe[fe <= 30], pe[fe <= 30], label=lab)
    plt.xlabel("modulation frequency (Hz)"); plt.ylabel("normalised modulation PSD (dB)")
    plt.title("Modulation spectrum of 300-3400 Hz envelope (48 kHz capture)")
    plt.legend(fontsize=8); plt.grid(alpha=.3); plt.tight_layout()
    plt.savefig(os.path.join(HERE, "fig_modspec.png"), dpi=130); plt.close()

    # per-feature AUC bars
    names = [r["feature"] for r in auc_rows]
    a48 = [r["auc_48k"] for r in auc_rows]; a16 = [r["auc_16k"] for r in auc_rows]
    a48s = [r["auc_48k_speech"] for r in auc_rows]
    yy = np.arange(len(names))
    plt.figure(figsize=(8, 6))
    plt.barh(yy - 0.25, a48, 0.25, label="48 kHz, all attacks")
    plt.barh(yy, a16, 0.25, label="16 kHz, all attacks")
    plt.barh(yy + 0.25, a48s, 0.25, label="48 kHz, real-speech modulator only")
    plt.yticks(yy, names, fontsize=8); plt.axvline(0.5, color="k", lw=.8); plt.xlim(0, 1)
    plt.xlabel("AUC (attack vs benign; <0.5 means inverted)"); plt.legend(fontsize=8)
    plt.tight_layout(); plt.savefig(os.path.join(HERE, "fig_feature_auc.png"), dpi=130); plt.close()


if __name__ == "__main__":
    main()
