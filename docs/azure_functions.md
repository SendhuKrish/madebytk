# Running on Azure Functions

`function_app.py` serves the FastAPI app over HTTP and runs the two cron jobs as
timer triggers. The VM path (Docker + APScheduler) keeps working: APScheduler
only starts when `SCHEDULER_ENABLED` is true (the default).

## Function App settings

`.env.config` is packaged with the app and supplies non-secret defaults; app
settings override it. Set these on the Function App:

| Setting | Value | Notes |
|---|---|---|
| `SCHEDULER_ENABLED` | `false` | Timers replace APScheduler |
| `PREDICT_SCHEDULE` | `0 0 0 * * 1,4` | NCRONTAB, **UTC**: 08:00 SGT Mon/Thu |
| `RESULTS_SCHEDULE` | `0 0 11-14 * * *` | 19:00–22:00 SGT hourly, daily |
| `SUPABASE_URL`, `SUPABASE_KEY`, `ANTHROPIC_API_KEY` | secrets | Never in git |

## CORS

On Functions the **platform** CORS setting (Function App → API → CORS) must answer
preflight: the Functions host handles `OPTIONS` before FastAPI sees it. Add
`https://madebytk.com`, `https://www.madebytk.com` and the Static Web Apps
hostname there. Do **not** set `CORS_ORIGINS` on the Function App, or requests
get duplicate `Access-Control-Allow-Origin` headers, which browsers reject.

SGT is UTC+8 with no DST, so the UTC schedules are stable. If you change
`RESULTS_HOUR` / `RESULTS_RETRY_UNTIL_HOUR`, change `RESULTS_SCHEDULE` to match
(the last run of the day is the one that raises on missing results).

## Scale settings (Flex Consumption)

Function App → Scale and concurrency. These values matter:

| Setting | Value |
|---|---|
| Instance memory | 2048 MB |
| HTTP concurrency | **Assign manually: 16** |
| On-demand maximum instance count | **4** |
| Always-ready instances | none (about $20/month at 2 GB, more than the VM it replaces) |

With HTTP concurrency on "system-assigned" and the max instance count at 1–2, the
platform held requests before they reached the Functions host: random 60 s hangs
ending in 503, or a fixed ~5 s on every request once an always-ready instance was
added. App Insights showed the host handling each request in about 3 ms, so the
delay was upstream of the host and not in the app. Setting both values above fixed
it. After 10 idle minutes the first request takes about 4 s (cold start), then
about 0.13 s.

## Parallel run with the VM

Predictions are random, so two live schedulers would overwrite each other. While
the VM is live, keep both timers off on the Function App:

    AzureWebJobs.predict_timer.Disabled = true
    AzureWebJobs.results_timer.Disabled = true

At cutover: stop the VM scheduler (`docker compose down`), then remove those two
settings.

## Local run

    cp local.settings.example.json local.settings.json   # fill in secrets
    azurite &                                            # or a real storage account
    func start

Fire a timer by hand: `POST http://localhost:7071/admin/functions/results_timer`
with body `{}` and header `Content-Type: application/json`.
