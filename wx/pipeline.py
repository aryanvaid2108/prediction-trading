"""Glue: train Mixed EMOS and produce a calibrated (mu, sigma) for a target day,
optionally sharpened intraday by the temperatures already observed today."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np

import dataclasses

import pandas as pd
from scipy.stats import norm

from . import cli, emos, intraday, obs, trading
from .backtest import (build_archive_table_wide, calibration_factor,
                       rolling_score_mixed)
from .forecast import (ensemble_features, fetch_members_archive,
                       fetch_members_forecast, member_daily_highs)
from .settlement import lst_day
from .stations import Station


def quote(st: Station, target: date, train_days: int = 75, window: int = 45,
          ridge: float = 0.5):
    """Return (mu, sigma) for the daily high on `target` from Mixed EMOS.

    Trains on the trailing multi-model archive up to the day before target, then
    applies the fit to the live multi-model forecast for target.
    """
    table, cols = build_archive_table_wide(st, target - timedelta(days=train_days),
                                           target - timedelta(days=1))
    tr = table.tail(window)
    model = emos.fit_mixed(tr[cols].to_numpy(),
                           (tr["ens_std"].to_numpy() ** 2).reshape(-1, 1),
                           tr["high"].to_numpy(), ridge=ridge)

    members = fetch_members_forecast(st.lat, st.lon,
                                     forecast_days=(target - date.today()).days + 2)
    wide = member_daily_highs(members, st.std_utc_offset).pivot(
        index="day", columns="member", values="high")
    feats = ensemble_features(members, st.std_utc_offset).set_index("day")
    if target not in wide.index:
        raise ValueError(f"no forecast for {target}")
    Xmean = wide.loc[target, cols].to_numpy().reshape(1, -1)
    Xvar = np.array([[feats.loc[target, "ens_std"] ** 2]])
    mu, sigma = model.predict(Xmean, Xvar)
    return float(mu[0]), float(sigma[0])


@dataclass
class LiveQuote:
    mu: float            # blended predictive mean
    sigma: float         # blended predictive sd
    prob_fn: object      # prob(lo, hi) -> probability, floored at obs max
    observed_max: float  # highest temp seen so far today (None if none yet)
    hour_lst: int        # current Local-Standard-Time hour
    mu_prior: float      # forecast-only mean (Mixed EMOS, calibrated)
    sigma_prior: float
    calib: float         # sigma inflation applied to the prior
    intraday_active: bool


def quote_live(st: Station, target: date = None, now_utc: datetime = None,
               train_days: int = 75, window: int = 45, ridge: float = 0.5) -> LiveQuote:
    """Forecast prior blended with today's observations so far.

    Precision-weighted blend: in the morning the intraday residual spread is wide
    so the forecast dominates; by afternoon it collapses toward the observed max
    and the observations dominate. The result is floored at the observed max.
    """
    target = target or date.today()
    now_utc = now_utc or datetime.now(timezone.utc)

    # --- forecast prior (Mixed EMOS) + data-driven sigma calibration ---
    table, cols = build_archive_table_wide(st, target - timedelta(days=train_days),
                                           target - timedelta(days=1))
    tr = table.tail(window)
    model = emos.fit_mixed(tr[cols].to_numpy(), (tr["ens_std"].to_numpy() ** 2).reshape(-1, 1),
                           tr["high"].to_numpy(), ridge=ridge)
    scored = rolling_score_mixed(table, cols, min_train=45, window=window, ridge=ridge)
    calib = calibration_factor(scored)

    if target >= date.today():
        members = fetch_members_forecast(st.lat, st.lon, forecast_days=(target - date.today()).days + 2)
    else:  # historical simulation: use the archived forecast for that day
        members = fetch_members_archive(st.lat, st.lon, target, target)
    wide = member_daily_highs(members, st.std_utc_offset).pivot(index="day", columns="member", values="high")
    feats = ensemble_features(members, st.std_utc_offset).set_index("day")
    Xmean = wide.loc[target, cols].to_numpy().reshape(1, -1)
    mu0, s0 = model.predict(Xmean, np.array([[feats.loc[target, "ens_std"] ** 2]]))
    mu0, s0 = float(mu0[0]), float(s0[0]) * calib

    # --- today's observations so far ---
    hour_lst = int((now_utc + timedelta(hours=st.std_utc_offset)).hour)
    now_naive = now_utc.replace(tzinfo=None)
    today_obs = obs.fetch_asos(st.iem_id, target, target + timedelta(days=1))
    today_obs = today_obs[(lst_day(today_obs["valid"], st.std_utc_offset) == target)
                          & (today_obs["valid"] <= now_naive)].dropna(subset=["tmpf"])
    observed_max = float(today_obs["tmpf"].max()) if len(today_obs) else None

    if observed_max is None:
        return LiveQuote(mu0, s0, trading.gaussian_prob(mu0, s0), None, hour_lst,
                         mu0, s0, calib, intraday_active=False)

    # --- intraday residual model for the current hour ---
    # Residual targets the official CLI high, so the intraday path carries the same
    # settlement basis as the forecast prior (both trained on CLI, not raw ASOS).
    hist = obs.fetch_asos(st.iem_id, target - timedelta(days=train_days), target)
    prep = intraday.prep(hist, st.std_utc_offset, [hour_lst])
    prep["day"] = pd.to_datetime(prep["day"])
    rm = prep.set_index("day")[f"rm_{hour_lst}"]
    try:
        finals = cli.settlement_high(st.icao, target - timedelta(days=train_days), target - timedelta(days=1))
    except Exception:
        finals = prep.set_index("day")["final"]   # ASOS reconstruction fallback
    res_all = (finals - rm).dropna()
    res = res_all.tail(60).to_numpy()
    if len(res) < 15:
        return LiveQuote(mu0, s0, trading.gaussian_prob(mu0, s0), observed_max, hour_lst,
                         mu0, s0, calib, intraday_active=False)

    # --- honest mixture (validated in scripts/honest_eval) ---
    # Mixture of prior samples and empirical residual samples, NOT a precision
    # product: a mixture's spread never collapses below its components, and
    # prior-vs-obs disagreement widens it. A walk-forward sharpening factor
    # (trailing std of past raw PIT-z, biased 1.1x wide for safety) sets the
    # final spread. Eval: std(z) 1.25->0.97, cov90 85%->92%, CRPS unchanged.
    N, BW = 2000, 0.25
    rng = np.random.default_rng(0)

    def raw_mix(mu_p, s_p, res_arr, omax):
        wi_ = 1.0 / max(float(res_arr.std()), 0.3) ** 2
        w0_ = 1.0 / s_p ** 2
        n0 = int(round(N * w0_ / (w0_ + wi_)))
        pr = rng.normal(mu_p, s_p, n0)
        it = omax + rng.choice(res_arr, N - n0, replace=True)
        return np.concatenate([pr, it]) + rng.normal(0, BW, N)

    sc = scored.set_index(pd.to_datetime(scored["day"]))
    zs = []
    for d in sc.index.intersection(rm.index).intersection(finals.index):
        past = res_all[res_all.index < d].tail(60)
        if len(past) < 20 or pd.isna(rm.loc[d]):
            continue
        raw = np.clip(raw_mix(float(sc.loc[d, "mu"]), float(sc.loc[d, "sigma"]) * calib,
                              past, float(rm.loc[d])), round(float(rm.loc[d])) - 0.5, None)
        pit = float(np.clip((raw < float(finals.loc[d])).mean(), 1e-4, 1 - 1e-4))
        zs.append(float(norm.ppf(pit)))
    shrink = float(np.clip(np.std(zs) * 1.1, 0.7, 1.3)) if len(zs) >= 25 else 1.0

    samples = raw_mix(mu0, s0, res_all.tail(60), observed_max)
    samples = samples.mean() + (samples - samples.mean()) * shrink
    samples = np.clip(samples, round(observed_max) - 0.5, None)
    mu_b, s_b = float(samples.mean()), float(samples.std())
    prob_fn = trading.sample_prob(samples)
    return LiveQuote(mu_b, s_b, prob_fn, observed_max, hour_lst, mu0, s0, calib, intraday_active=True)


def widen_for_afd(q: LiveQuote, st: Station, target: date) -> LiveQuote:
    """Widen a quote's sigma per the AFD confidence signal, rebuilding prob_fn.

    Only inflates uncertainty (never shifts the mean); on any failure returns the
    quote unchanged so the loop never breaks on an LLM/API hiccup.
    """
    from . import afd
    try:
        signal = afd.parse_afd(afd.fetch_afd(st.wfo), st.name, target)
        factor = afd.sigma_factor(signal)
    except Exception:
        return q
    if factor <= 1.0:
        return q
    s = q.sigma * factor
    prob_fn = (trading.floored_gaussian_prob(q.mu, s, q.observed_max)
               if q.observed_max is not None else trading.gaussian_prob(q.mu, s))
    return dataclasses.replace(q, sigma=s, prob_fn=prob_fn)
