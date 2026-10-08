"""One-class models on benign features; evaluate on held-out benign, proxy, DolphinAttack per device."""
import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from sklearn.ensemble import IsolationForest
from sklearn.decomposition import PCA
from sklearn.metrics import roc_auc_score
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

D = os.path.dirname(os.path.abspath(__file__))
z = np.load(f'{D}/features.npz', allow_pickle=True)
X, S, C, DEV, CLIP, SPL, NAMES = (z[k] for k in ['X', 'set', 'category', 'device', 'clip', 'split', 'names'])
SPEECH = np.isin(C, ['speech_clean', 'speech_noisy', 'speech_noisy_mix'])
DEVICES = ['google-pixel', 'oppo-reno', 'huawei-mate9', 'huawei-nova2', 'oppo-k3']


class Gauss:
    def fit(self, Z):
        self.mu = Z.mean(0); self.lw = LedoitWolf().fit(Z); self.P = self.lw.precision_; return self
    def score(self, Z):
        d = Z - self.mu; return np.einsum('ij,jk,ik->i', d, self.P, d)
    def contrib(self, Z):  # per-dimension Mahalanobis terms (sum = score)
        d = Z - self.mu; return d * (d @ self.P)


class PCARec:
    def fit(self, Z):
        self.p = PCA(n_components=0.95).fit(Z); return self
    def score(self, Z):
        R = self.p.inverse_transform(self.p.transform(Z)); return ((Z - R) ** 2).sum(1)


class IF:
    def fit(self, Z):
        self.m = IsolationForest(n_estimators=500, random_state=0, contamination='auto').fit(Z); return self
    def score(self, Z): return -self.m.score_samples(Z)


def run(variant):
    train_mask = (S == 'benign') & (SPL == 'train') & (SPEECH if variant == 'speech' else True)
    mu, sd = X[train_mask].mean(0), X[train_mask].std(0) + 1e-9
    Z = (X - mu) / sd
    # held-out benign in the trained categories (used for threshold + AUC negatives)
    ho = (S == 'benign') & (SPL == 'test') & (SPEECH if variant == 'speech' else True)
    sets = {
        'heldout_benign_speech': (S == 'benign') & (SPL == 'test') & SPEECH,
        'heldout_benign_nonspeech': (S == 'benign') & (SPL == 'test') & ~SPEECH,
        'proxy_heldout_speech': (S == 'proxy') & (SPL == 'test'),
    }
    for d in DEVICES: sets[f'attack_{d}'] = (S == 'attack') & (DEV == d)
    out = []; scores = {}
    for mname, M in [('Mahalanobis', Gauss), ('IsolationForest', IF), ('PCA_recon', PCARec)]:
        m = M().fit(Z[train_mask]); sc = m.score(Z); scores[mname] = sc
        thr = np.quantile(sc[ho], 0.99)
        for sname, mask in sets.items():
            neg = sc[ho]; pos = sc[mask]
            auc = roc_auc_score(np.r_[np.zeros(len(neg)), np.ones(len(pos))], np.r_[neg, pos]) if sname.startswith('attack') or 'nonspeech' in sname or 'proxy' in sname else np.nan
            out.append(dict(variant=variant, model=mname, set=sname, n=int(mask.sum()),
                            auc_vs_heldout=round(float(auc), 3) if not np.isnan(auc) else '',
                            rate_at_1pctFP=round(float((pos > thr).mean()), 3)))
        if mname == 'Mahalanobis':
            px = (S == 'attack') & (DEV == 'google-pixel')
            ct = m.contrib(Z[px]).mean(0)
            zabs = np.abs(Z[px]).mean(0); zmean = Z[px].mean(0)
            top = np.argsort(-ct)[:10]
            print(f'\n[{variant}] Mahalanobis: top-10 contributing dims on pixel (mean term, share of score, mean std-z):')
            for i in top:
                print(f'  {NAMES[i]:24s} term={ct[i]:9.1f} share={ct[i]/ct.sum():.3f} mean_z={zmean[i]:+.2f}')
            top2 = np.argsort(-zabs)[:8]
            print('  largest mean |z| dims:', ', '.join(f'{NAMES[i]}({zmean[i]:+.1f})' for i in top2))
            print(f'  median score: heldout {np.median(sc[ho]):.0f}, proxy {np.median(sc[sets["proxy_heldout_speech"]]):.0f}, pixel {np.median(sc[px]):.0f}, thr@1%FP {thr:.0f}')
    # plot
    sc = scores['Mahalanobis']; fig, ax = plt.subplots(figsize=(8, 4.5))
    bins = np.linspace(np.log10(sc.min() + 1), np.log10(np.percentile(sc, 99.5)), 60)
    for sname, mask in sets.items():
        ax.hist(np.log10(sc[mask] + 1), bins=bins, density=True, histtype='step', lw=1.5, label=f'{sname} (n={mask.sum()})')
    ax.axvline(np.log10(np.quantile(sc[ho], 0.99) + 1), color='k', ls='--', label='1% FP threshold')
    ax.set_xlabel('log10 Mahalanobis score'); ax.set_ylabel('density'); ax.set_title(f'One-class Gaussian trained on benign {variant}')
    ax.legend(fontsize=7); fig.tight_layout(); fig.savefig(f'{D}/scores_mahalanobis_{variant}.png', dpi=130)
    return pd.DataFrame(out)


df = pd.concat([run('speech'), run('all')])
df.to_csv(f'{D}/results.csv', index=False)
for v in ['speech', 'all']:
    print(f'\n=== variant: train on benign {v} ===')
    t = df[df.variant == v].pivot(index='set', columns='model', values=['auc_vs_heldout', 'rate_at_1pctFP'])
    print(t.to_string())
