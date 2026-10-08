"""Baseline anti-spoofing detector: standardised features + logistic regression.

A deliberately simple, transparent classifier so the benchmark is reproducible
and the numbers are honest about being a baseline. Reports the ASVspoof-standard
Equal Error Rate (EER).

scikit-learn is an optional dependency: `pip install echoguard[spoof]`.
"""

from __future__ import annotations

import numpy as np

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "echoguard.spoof needs scikit-learn: pip install 'echoguard[spoof]'"
    ) from exc


class SpoofDetector:
    """Train on (features, label) where label 1 = spoof, 0 = genuine."""

    def __init__(self):
        self.scaler = StandardScaler()
        self.clf = LogisticRegression(max_iter=1000, class_weight="balanced")

    def fit(self, X: np.ndarray, y: np.ndarray) -> SpoofDetector:
        Xs = self.scaler.fit_transform(X)
        self.clf.fit(Xs, y)
        return self

    def score(self, X: np.ndarray) -> np.ndarray:
        """Return spoof probability in [0, 1] per row."""
        Xs = self.scaler.transform(X)
        return self.clf.predict_proba(Xs)[:, 1]


def equal_error_rate(scores, labels) -> float:
    """EER: the error rate where false-accept rate equals false-reject rate.

    labels: 1 = spoof (positive), 0 = genuine. Scores are "spoofness".

    Computed from the ROC by a single sort (O(N log N)), with linear
    interpolation at the crossing of the miss and false-alarm curves, as in
    the ASVspoof reference `compute_eer`.
    """
    scores = np.asarray(scores, dtype=np.float64).ravel()
    labels = np.asarray(labels).ravel().astype(int)
    if scores.size == 0 or scores.size != labels.size:
        raise ValueError("scores and labels must be non-empty and the same length")
    n_pos = int((labels == 1).sum())
    n_neg = int((labels == 0).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("EER needs at least one spoof and one genuine trial")

    order = np.argsort(-scores, kind="mergesort")
    sorted_labels = labels[order]
    # Threshold sweeps from +inf down: everything above is called spoof.
    tp = np.cumsum(sorted_labels == 1)
    fp = np.cumsum(sorted_labels == 0)
    far = fp / n_neg                 # genuine called spoof
    frr = 1.0 - tp / n_pos           # spoof called genuine
    far = np.concatenate([[0.0], far])
    frr = np.concatenate([[1.0], frr])

    diff = frr - far
    idx = int(np.argmax(diff <= 0))
    if idx == 0:
        return float((far[0] + frr[0]) / 2)
    # Interpolate between the last point with frr > far and the first with frr <= far.
    d0, d1 = diff[idx - 1], diff[idx]
    t = d0 / (d0 - d1) if d0 != d1 else 0.0
    eer_far = far[idx - 1] + t * (far[idx] - far[idx - 1])
    eer_frr = frr[idx - 1] + t * (frr[idx] - frr[idx - 1])
    return float((eer_far + eer_frr) / 2)
