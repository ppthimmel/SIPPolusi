import time
import logging
import schedule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def job():
    logger.info("Running data worker job: fetching OSM and satellite data...")
    # Placeholder for GeoPandas, rasterio, xarray logic

schedule.every(3).hours.do(job)

if __name__ == "__main__":
    logger.info("Data worker started.")
    job() # run once immediately
    while True:
        schedule.run_pending()
        time.sleep(60)
