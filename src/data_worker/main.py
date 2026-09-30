import time
import logging
import schedule

from database import init_db
from spatial_model.inference import run_downscale_inference, run_idw_fallback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def job():
    logger.info("Running data worker job: fetching OSM and satellite data...")
    # Placeholder for GeoPandas, rasterio, xarray logic

    time_window = time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime())
    try:
        result = run_downscale_inference(time_window)
        logger.info(f"Downscale inference summary: {result}")
    except Exception as e:
        logger.error(f"Downscale inference failed, falling back to IDW: {e}")
        run_idw_fallback(time_window)

schedule.every(3).hours.do(job)

if __name__ == "__main__":
    logger.info("Data worker started.")
    init_db()  # PostGIS extension, schemas and tables (owned by the Data Worker)
    job() # run once immediately
    while True:
        schedule.run_pending()
        time.sleep(60)
