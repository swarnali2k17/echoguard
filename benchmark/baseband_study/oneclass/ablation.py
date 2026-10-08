"""Ablation: (a) proxy with single-pass 4th-order bandpass (sosfilt, not filtfilt);
(b) Mahalanobis speech-variant with the 2-20 Hz ratio feature dropped. Segment-level."""
import sys
import numpy as np
import pandas as pd
from scipy.signal import butter, sosfilt
D = os.path.dirname(os.path.abspath(__file__))
S = os.environ.get("ECHOGUARD_STUDY_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))  # dir holding fork/, realaudio/, dolphin/
sys.path.insert(0, f'{S}/fork')
# reuse feature code without re-running extraction
src = open(f'{D}/features.py').read().split('rows = []')[0]
ns = {}; exec(src, ns)
features, peak_norm, segments, to16k, SEG, SR = (ns[k] for k in ['features', 'peak_norm', 'segments', 'to16k', 'SEG', 'SR'])
from echoguard.audio import load_wav
from model import Gauss

z = np.load(f'{D}/features.npz', allow_pickle=True)
X, Sset, C, DEV, CLIP, SPL = (z[k] for k in ['X', 'set', 'category', 'device', 'clip', 'split'])
SPEECH = np.isin(C, ['speech_clean', 'speech_noisy', 'speech_noisy_mix'])
# dolphin level distributions
att = (Sset == 'attack')
rng = np.random.default_rng(1)
d_rms = []; d_nf = []
import glob
for f in sorted(glob.glob(f'{S}/dolphin/data/dolphin-dataset/*.wav'))[::5]:
    x, _ = load_wav(f); d_rms.append(x.std())
    fr_ = x[:len(x)//160*160].reshape(-1, 160); d_nf.append(np.percentile(np.sqrt((fr_**2).mean(1)), 10))
d_rms, d_nf = np.array(d_rms), np.array(d_nf)
sos = butter(4, [100, 7000], btype='band', fs=SR, output='sos')
man = pd.read_csv(f'{S}/realaudio/corpus/manifest.csv')
test_speech_clips = set(CLIP[(Sset == 'proxy') & (SPL == 'test')])
P2 = []
for _, r in man.iterrows():
    if r.file not in test_speech_clips: continue
    x, sr = load_wav(f'{S}/realaudio/corpus/{r.file}'); x = to16k(x, sr)
    for s in segments(x, SEG):
        y = sosfilt(sos, s); i = rng.integers(len(d_rms))
        y = y * (d_rms[i] / (y.std() + 1e-12))
        n = sosfilt(sos, rng.standard_normal(SEG)); y = y + n * (d_nf[i] / n.std())
        P2.append(features(peak_norm(y)))
P2 = np.array(P2)
print('single-pass proxy: median r2_20 =', np.median(P2[:, 81]).round(1), ' sub100 =', np.median(P2[:, 80]).round(4))

train = (Sset == 'benign') & (SPL == 'train') & SPEECH
ho = (Sset == 'benign') & (SPL == 'test') & SPEECH
proxy_ff = (Sset == 'proxy') & (SPL == 'test')
for label, keep in [('all 83 feats', np.ones(83, bool)), ('drop r2_20', np.arange(83) != 81),
                    ('drop r2_20+sub100', ~np.isin(np.arange(83), [80, 81]))]:
    mu, sd = X[train][:, keep].mean(0), X[train][:, keep].std(0) + 1e-9
    m = Gauss().fit((X[train][:, keep] - mu) / sd)
    def sc(A):
        return m.score((A[:, keep] - mu) / sd)
    thr = np.quantile(sc(X[ho]), 0.99)
    row = {'features': label, 'proxy_filtfilt': (sc(X[proxy_ff]) > thr).mean(), 'proxy_singlepass': (sc(P2) > thr).mean()}
    for d in ['google-pixel', 'oppo-reno', 'huawei-mate9', 'huawei-nova2', 'oppo-k3']:
        row[d] = (sc(X[(Sset == 'attack') & (DEV == d)]) > thr).mean()
    print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()})
