"""Azure Functions entry point.

The FastAPI app is served over HTTP (ASGI), and the two cron jobs run as timer
triggers. Set SCHEDULER_ENABLED=false in the Function App settings so
APScheduler doesn't also start. Timer schedules live in app settings
(NCRONTAB, UTC) — see docs/azure_functions.md.
"""

import azure.functions as func

from app.jobs.predict import main as predict_main
from app.jobs.results import main as results_main
from app.main import app as fastapi_app

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)


# use_monitor=False: don't fire a "missed" run after a restart — predictions
# are random, so an extra run would overwrite good ones.
@app.timer_trigger(schedule="%PREDICT_SCHEDULE%", arg_name="timer", use_monitor=False)
async def predict_timer(timer: func.TimerRequest) -> None:
    await predict_main()


@app.timer_trigger(schedule="%RESULTS_SCHEDULE%", arg_name="timer", use_monitor=False)
async def results_timer(timer: func.TimerRequest) -> None:
    await results_main()
