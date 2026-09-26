# NWS fixtures — gridpoint LOX/154,44 (LAX)

Hand-built to the `api.weather.gov` response schema, **not recorded**:
`api.weather.gov` was refused by the build session's egress policy on
2026-09-25, so no live body could be captured. Each file carries a
`_provenance` key saying so. Shapes follow the published API (points,
forecast, forecast/hourly, gridpoint stations, stations/{id}/observations/latest,
alerts/active); values are chosen so tests can pin them:

- `forecast.json` carries `temperatureUnit: "F"` (the tests prove conversion by
  the unit the body carries); `forecastHourly.json` is SI (°C, km/h).
- Day 1 `hi` 77 °F → 25.0 °C, `lo` 63 °F → 17.2 °C; every day's hi ≠ its lo.
- 60 hourly periods (the route keeps 48), 14 forecast periods (→ 7 day rows).
- The observation is KLAX at 20:53Z, 21.1 °C, W 14.8 km/h, FEW.

Replace with recorded bodies when a session can reach `api.weather.gov`; the
tests pin behaviour, and the pins that are fixture values are named in them.
