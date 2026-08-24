"""Glue: train Mixed EMOS and produce a calibrated (mu, sigma) for a target day,
optionally sharpened intraday by the temperatures already observed today."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np

import dataclasses

import pandas as pd

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
    calib = calibration_factor(rolling_score_mixed(table, cols, min_train=45, window=window, ridge=ridge))

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
        res = (finals - rm).dropna().to_numpy()
    except Exception:
        res = intraday.residuals(prep, hour_lst)
    if len(res) < 15:
        return LiveQuote(mu0, s0, trading.gaussian_prob(mu0, s0), observed_max, hour_lst,
                         mu0, s0, calib, intraday_active=False)
    mu_i = observed_max + float(np.mean(res))
    s_i = max(float(np.std(res)), 0.3)

    # --- precision-weighted blend, floored at the observed max ---
    w0, wi = 1.0 / s0 ** 2, 1.0 / s_i ** 2
    mu_b = (w0 * mu0 + wi * mu_i) / (w0 + wi)
    s_b = float(np.sqrt(1.0 / (w0 + wi)))
    prob_fn = trading.floored_gaussian_prob(mu_b, s_b, observed_max)
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
