from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

_INV_SQRT_PI = 1.0 / np.sqrt(np.pi)


def crps_gaussian(y, mu, sigma):
    """Closed-form CRPS for a Gaussian predictive distribution (lower is better)."""
    sigma = np.maximum(sigma, 1e-6)
    z = (y - mu) / sigma
    return sigma * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - _INV_SQRT_PI)


def crps_samples(samples, y) -> float:
    """CRPS of an empirical (ensemble) distribution, O(n log n).

    CRPS = E|X - y| - 0.5 E|X - X'|, using the sorted-sample identity
    sum_ij |x_i - x_j| = 2 * sum_i (2i - n - 1) x_i.
    """
    x = np.sort(np.asarray(samples, float))
    n = len(x)
    e_xy = np.abs(x - y).mean()
    i = np.arange(1, n + 1)
    e_xx = (2.0 / n ** 2) * np.sum((2 * i - n - 1) * x)
    return float(e_xy - 0.5 * e_xx)


@dataclass
class EMOS:
    """Non-homogeneous Gaussian regression: N(a + b*mean, c + d*spread^2)."""
    a: float
    b: float
    c: float
    d: float

    def predict(self, ens_mean, ens_std):
        mu = self.a + self.b * np.asarray(ens_mean, float)
        var = self.c + self.d * np.asarray(ens_std, float) ** 2
        return mu, np.sqrt(np.maximum(var, 1e-6))

    def prob_ge(self, ens_mean, ens_std, threshold):
        mu, sigma = self.predict(ens_mean, ens_std)
        return 1.0 - norm.cdf((threshold - mu) / sigma)

    def prob_bucket(self, ens_mean, ens_std, lo, hi):
        """P(lo <= high <= hi) for an integer-degree bucket [lo, hi].

        Kalshi buckets are whole degrees, so the continuous distribution is
        integrated over [lo-0.5, hi+0.5] to match rounding at settlement.
        """
        mu, sigma = self.predict(ens_mean, ens_std)
        return norm.cdf((hi + 0.5 - mu) / sigma) - norm.cdf((lo - 0.5 - mu) / sigma)


@dataclass
class MixedEMOS:
    """Multi-predictor NGR: N(a + Xmean·b, c + Xvar·d).

    Xmean holds several mean predictors (e.g. each model's forecast, or a second
    ensemble's mean); Xvar holds spread predictors (already squared). Generalises
    EMOS so extra ensembles/models enter as additional columns.
    """
    a: float
    b: np.ndarray
    c: float
    d: np.ndarray

    def predict(self, Xmean, Xvar):
        Xmean = np.atleast_2d(np.asarray(Xmean, float))
        Xvar = np.atleast_2d(np.asarray(Xvar, float))
        mu = self.a + Xmean @ self.b
        var = self.c + Xvar @ self.d
        return mu, np.sqrt(np.maximum(var, 1e-6))

    def _mu_sigma(self, Xmean, Xvar):
        mu, sigma = self.predict(Xmean, Xvar)
        return float(mu[0]), float(sigma[0])

    def prob_bucket(self, Xmean, Xvar, lo, hi):
        mu, sigma = self._mu_sigma(Xmean, Xvar)
        return norm.cdf((hi + 0.5 - mu) / sigma) - norm.cdf((lo - 0.5 - mu) / sigma)

    def prob_ge(self, Xmean, Xvar, t):
        mu, sigma = self._mu_sigma(Xmean, Xvar)
        return 1.0 - norm.cdf((t - mu) / sigma)


def fit_mixed(Xmean, Xvar, y, ridge: float = 1.0) -> MixedEMOS:
    """Fit multi-predictor NGR by CRPS, shrinking mean weights toward an equal
    blend (1/K). ridge->inf recovers equal-weight single-predictor EMOS, so the
    model can only beat the baseline when the data supports it."""
    Xmean = np.atleast_2d(np.asarray(Xmean, float))
    Xvar = np.atleast_2d(np.asarray(Xvar, float))
    y = np.asarray(y, float)
    n, K = Xmean.shape
    J = Xvar.shape[1]
    prior = np.full(K, 1.0 / K)

    def unpack(p):
        return p[0], p[1:1 + K], p[1 + K], p[2 + K:]

    def loss(p):
        a, b, c, d = unpack(p)
        mu = a + Xmean @ b
        sigma = np.sqrt(np.maximum(c + Xvar @ d, 1e-6))
        return crps_gaussian(y, mu, sigma).mean() + ridge * np.sum((b - prior) ** 2)

    x0 = np.concatenate([[float(np.mean(y - Xmean @ prior))], prior, [1.0], np.full(J, 0.5)])
    bounds = [(None, None)] + [(None, None)] * K + [(1e-4, None)] + [(0, None)] * J
    res = minimize(loss, x0, method="L-BFGS-B", bounds=bounds)
    a, b, c, d = unpack(res.x)
    return MixedEMOS(a, np.array(b), c, np.array(d))


def fit(ens_mean, ens_std, y) -> EMOS:
    """Fit EMOS coefficients by minimizing mean CRPS on the training window."""
    ens_mean = np.asarray(ens_mean, float)
    ens_std = np.asarray(ens_std, float)
    y = np.asarray(y, float)

    def loss(p):
        a, b, c, d = p
        mu = a + b * ens_mean
        var = c + d * ens_std ** 2
        sigma = np.sqrt(np.maximum(var, 1e-6))
        return crps_gaussian(y, mu, sigma).mean()

    # init: pass-through mean, spread scaled to variance
    x0 = [float(np.mean(y - ens_mean)), 1.0, 1.0, 1.0]
    bounds = [(None, None), (0, None), (1e-4, None), (0, None)]
    res = minimize(loss, x0, method="L-BFGS-B", bounds=bounds)
    a, b, c, d = res.x
    return EMOS(a, b, c, d)
