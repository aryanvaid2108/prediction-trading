"""Price today's / tomorrow's high-temp buckets with a calibrated EMOS.

Trains on the multi-model archive (trailing window) and applies the fit to the
matching live multi-model forecast, so the ensemble-spread definition is
consistent between training and prediction.

Usage: python -m scripts.live_demo [ICAO]
"""
import sys
from datetime import date, timedelta

from wx import backtest, emos
from wx.forecast import ensemble_features, fetch_members_forecast
from wx.stations import get


def main(icao="KNYC"):
    st = get(icao)
    print(f"Station {icao} ({st.name})  IEM={st.iem_id}  LST offset={st.std_utc_offset}h  wfo={st.wfo}\n")

    end = date.today() - timedelta(days=1)
    train = backtest.build_archive_table(st, end - timedelta(days=75), end)
    train = train.dropna(subset=["ens_mean", "ens_std", "high"]).tail(45)
    model = emos.fit(train["ens_mean"], train["ens_std"], train["high"])
    print(f"Trained on {len(train)} days.  EMOS: mu = {model.a:.2f} + {model.b:.2f}*mean, "
          f"var = {model.c:.2f} + {model.d:.2f}*spread^2\n")

    fc = ensemble_features(fetch_members_forecast(st.lat, st.lon, forecast_days=2), st.std_utc_offset)
    fc = fc[fc["day"] >= date.today()].sort_values("day")
    for _, tgt in fc.iterrows():
        mu, sigma = model.predict(tgt["ens_mean"], tgt["ens_std"])
        mu, sigma = float(mu), float(sigma)
        print(f"{tgt['day']}  raw mean={tgt['ens_mean']:.1f}F spread={tgt['ens_std']:.1f}"
              f"  ->  N({mu:.1f}, {sigma:.1f}^2)")
        center = int(round(mu))
        for t in range(center - 3, center + 4):
            p = model.prob_bucket(tgt["ens_mean"], tgt["ens_std"], t, t)
            bar = "#" * round(p * 40)
            print(f"    {t:>3}F  {p:5.1%}  {bar}")
        print(f"    P(>= {center})={model.prob_ge(tgt['ens_mean'], tgt['ens_std'], center):.1%}   "
              f"P(>= {center+2})={model.prob_ge(tgt['ens_mean'], tgt['ens_std'], center+2):.1%}\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
