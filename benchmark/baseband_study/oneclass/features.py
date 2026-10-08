"""Extract per-1s-segment features for benign corpus, channel-matched proxy, and DolphinAttack.

Output: features.npz with X (N,83), plus string arrays set/category/device/clip/split.
"""
import sys
import os
import glob
import numpy as np
import pandas as pd
from scipy.signal import resample_poly, butter, sosfiltfilt, stft

S = os.environ.get("ECHOGUARD_STUDY_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))  # dir holding fork/, realaudio/, dolphin/
sys.path.insert(0, f'{S}/fork')
from echoguard.audio import load_wav

SR = 16000
SEG = SR  # 1 s
NMEL = 40
MAX_SEG_PER_CLIP = 20
rng = np.random.default_rng(0)


def mel_fb(sr=SR, nfft=512, nmel=NMEL, fmin=0, fmax=8000):
    def hz2mel(f): return 2595 * np.log10(1 + f / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    m = np.linspace(hz2mel(fmin), hz2mel(fmax), nmel + 2)
    hz = mel2hz(m)
    bins = np.floor((nfft + 1) * hz / sr).astype(int)
    fb = np.zeros((nmel, nfft // 2 + 1))
    for i in range(nmel):
        l, c, r = bins[i], bins[i + 1], bins[i + 2]
        if c == l: c = l + 1
        if r == c: r = c + 1
        fb[i, l:c] = (np.arange(l, c) - l) / (c - l)
        fb[i, c:r] = (r - np.arange(c, r)) / (r - c)
    return fb

FB = mel_fb()
FEAT_NAMES = [f'mel{i}_mean' for i in range(NMEL)] + [f'mel{i}_std' for i in range(NMEL)] + \
             ['sub100_frac', 'r2_20_vs_300_3400_dB', 'flatness_0_4k']


def peak_norm(x, target=0.5):
    p = np.abs(x).max()
    return x * (target / p) if p > 1e-9 else x


def features(x):
    """x: 1 s float mono at 16 kHz, already peak-normalised."""
    # 25 ms frames (400 samples), 10 ms hop, nfft 512
    f, t, Z = stft(x, fs=SR, nperseg=400, noverlap=240, nfft=512, boundary=None, padded=False)
    P = np.abs(Z) ** 2  # (257, T)
    mel = np.log(FB @ P + 1e-10)  # (40, T)
    mel_mean = mel.mean(1); mel_std = mel.std(1)
    # long-window spectrum for sub-100 Hz and 2-20 Hz quantities (1 Hz resolution)
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    fr = np.fft.rfftfreq(len(x), 1 / SR)
    tot = X.sum() + 1e-20
    sub100 = X[fr < 100].sum() / tot
    e_2_20 = X[(fr >= 2) & (fr <= 20)].sum() + 1e-20
    e_300_3400 = X[(fr >= 300) & (fr <= 3400)].sum() + 1e-20
    ratio_db = 10 * np.log10(e_2_20 / e_300_3400)
    Pavg = P.mean(1)
    band = Pavg[f <= 4000] + 1e-12
    flat = np.exp(np.mean(np.log(band))) / np.mean(band)
    return np.concatenate([mel_mean, mel_std, [sub100, ratio_db, flat]])


def segments(x, hop):
    n = len(x)
    if n < SEG:
        return []
    starts = list(range(0, n - SEG + 1, hop))
    if len(starts) > MAX_SEG_PER_CLIP:
        starts = [starts[i] for i in np.linspace(0, len(starts) - 1, MAX_SEG_PER_CLIP).astype(int)]
    return [x[s:s + SEG] for s in starts]


def to16k(x, sr):
    if sr == SR: return x
    from math import gcd
    g = gcd(sr, SR)
    return resample_poly(x, SR // g, sr // g)


rows = []  # (set, category, device, clip, split)
X = []

# ---------- benign ----------
man = pd.read_csv(f'{S}/realaudio/corpus/manifest.csv')
clips = man['file'].tolist()
perm = rng.permutation(len(clips))
# split by clip, stratified by category: 70% train
split = {}
for _cat, g in man.groupby('category'):
    idx = rng.permutation(g.index.values)
    ntr = int(round(0.7 * len(idx)))
    for j, i in enumerate(idx):
        split[man.loc[i, 'file']] = 'train' if j < ntr else 'test'

benign_audio = {}
for _, r in man.iterrows():
    x, sr = load_wav(f'{S}/realaudio/corpus/{r.file}')
    x = to16k(x, sr)
    benign_audio[r.file] = x
    for s in segments(x, SEG):
        X.append(features(peak_norm(s)))
        rows.append(('benign', r.category, '', r.file, split[r.file]))
print('benign segments', len(X))

# ---------- DolphinAttack ----------
dfiles = sorted(glob.glob(f'{S}/dolphin/data/dolphin-dataset/*.wav'))
d_rms = []; d_nf = []
nb = len(X)
for fpath in dfiles:
    name = os.path.basename(fpath)
    dev = name.split('+')[0]
    x, sr = load_wav(fpath)
    assert sr == SR
    d_rms.append(x.std())
    fr_ = x[:len(x) // 160 * 160].reshape(-1, 160)
    d_nf.append(np.percentile(np.sqrt((fr_ ** 2).mean(1)), 10))
    for s in segments(x, SEG // 2):
        X.append(features(peak_norm(s)))
        rows.append(('attack', 'dolphin', dev, name, 'test'))
print('attack segments', len(X) - nb)
d_rms = np.array(d_rms); d_nf = np.array(d_nf)

# ---------- channel-matched benign proxy (from held-out benign speech) ----------
sos = butter(4, [100, 7000], btype='band', fs=SR, output='sos')
nb = len(X)
speech_cats = {'speech_clean', 'speech_noisy', 'speech_noisy_mix'}
for _, r in man.iterrows():
    if r.category not in speech_cats: continue
    x = benign_audio[r.file]
    for s in segments(x, SEG):
        y = sosfiltfilt(sos, s)
        # scale speech RMS to a sample from the DolphinAttack clip RMS distribution
        i = rng.integers(len(d_rms))
        y = y * (d_rms[i] / (y.std() + 1e-12))
        # add noise at a sampled DolphinAttack noise-floor level (same channel filter)
        n = sosfiltfilt(sos, rng.standard_normal(SEG))
        n = n * (d_nf[i] / n.std())
        y = y + n
        X.append(features(peak_norm(y)))
        rows.append(('proxy', r.category, '', r.file, split[r.file]))
print('proxy segments', len(X) - nb)

X = np.array(X)
rows = np.array(rows, dtype=object)
np.savez(f'{S}/oneclass/features.npz', X=X, set=rows[:, 0].astype(str), category=rows[:, 1].astype(str),
         device=rows[:, 2].astype(str), clip=rows[:, 3].astype(str), split=rows[:, 4].astype(str),
         names=np.array(FEAT_NAMES))
print('saved', X.shape)
