import os
import sys
import json
import time
import logging
import gevent

from counterblock.lib import blockfeed, config, cache, siofeeds, database, util
from counterblock.lib.processor import StartUpProcessor, CORE_FIRST_PRIORITY, CORE_LAST_PRIORITY, api, tasks

logger = logging.getLogger(__name__)

#https://github.com/miracle2k/gevent-erlang-mode/blob/master/erlangmode/links.py
class LinkedFailed(Exception):
    """Raised when a linked greenlet dies because of unhandled exception"""

    msg = "%r failed with %s: %s"

    def __init__(self, source):
        exception = source.exception
        try:
            excname = exception.__class__.__name__
        except:
            excname = str(exception) or repr(exception)
        Exception.__init__(self, self.msg % (source, excname, exception))

@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 0)
def load_counterwallet_config_settings():
    #TODO: Hardcode in cw path for now. Will be taken out to a plugin shortly...
    counterwallet_config_path = os.path.join('/home/xcp/counterwallet/counterwallet.conf.json')
    if os.path.exists(counterwallet_config_path):
        logger.info("Loading counterwallet config at '%s'" % counterwallet_config_path)
        with open(counterwallet_config_path) as f:
            config.COUNTERWALLET_CONFIG_JSON = f.read()
    else:
        logger.warn("Counterwallet config does not exist at '%s'. Counterwallet functionality disabled..." % counterwallet_config_path)
        config.COUNTERWALLET_CONFIG_JSON = '{}'
    try:
        config.COUNTERWALLET_CONFIG = json.loads(config.COUNTERWALLET_CONFIG_JSON)
    except Exception, e:
        logger.error("Exception loading counterwallet config: %s" % e)
        
@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 2)
def init_geoip():
    import pygeoip
    
    def download_geoip_data():
        logger.info("Checking/updating GeoIP.dat ...")
    
        download = False;
        data_path = os.path.join(config.data_dir, 'GeoIP.dat')
        if not os.path.isfile(data_path):
            download = True
        else:
            one_week_ago = time.time() - 60*60*24*7
            file_stat = os.stat(data_path)
            if file_stat.st_ctime < one_week_ago:
                download = True
    
        if download:
            logger.info("Downloading GeoIP.dat")
            ##TODO: replace with pythonic way to do this!
            cmd = "cd '{}'; wget -N -q http://geolite.maxmind.com/download/geoip/database/GeoLiteCountry/GeoIP.dat.gz; gzip -dfq GeoIP.dat.gz".format(config.data_dir)
            util.subprocess_cmd(cmd)
        else:
            logger.info("GeoIP.dat database up to date. Not downloading.")
    
    download_geoip_data()
    config.GEOIP =  pygeoip.GeoIP(os.path.join(config.data_dir, 'GeoIP.dat'))

@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 3)
def init_mongo():
    config.mongo_db = database.get_connection() #should be able to access fine across greenlets, etc
    database.init_base_indexes(config.mongo_db)
    
@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 4)
def init_redis():
    config.REDIS_CLIENT = cache.get_redis_connection()
    
@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 5)
def init_siofeeds():
    siofeeds.set_up()

@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 6)
def start_cp_blockfeed():
    logger.info("Starting up counterparty block feed poller...")
    try:
        parent = gevent.getcurrent()
        g = gevent.spawn(blockfeed.process_cp_blockfeed, config.ZMQ_PUBLISHER_EVENTFEED)
        g.link_exception(lambda failed: gevent.kill(parent, LinkedFailed(failed)))
    except LinkedFailed, e:
        logger.exception(e)
        time.sleep(2)
        start_cp_blockfeed()
    
@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 7)
def check_blockchain_service():
    logger.debug("Starting event timer: check_blockchain_service")
    gevent.spawn(tasks.check_blockchain_service)
    
@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 8)
def expire_stale_prefs():
    logger.debug("Starting event timer: expire_stale_prefs")
    gevent.spawn(tasks.expire_stale_prefs)

@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 9)
def expire_stale_orders():
    logger.debug("Starting event timer: expire_stale_btc_open_order_records")
    gevent.spawn(tasks.expire_stale_btc_open_order_records)

@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 10)
def generate_wallet_stats():
    logger.debug("Starting event timer: generate_wallet_stats")
    gevent.spawn(tasks.generate_wallet_stats)
    
@StartUpProcessor.subscribe(priority=CORE_FIRST_PRIORITY - 11)
def warn_on_missing_support_email():
    if not config.SUPPORT_EMAIL:
        logger.warn("Support email setting not set: To enable, please specify an email for the 'support-email' setting in your counterblockd.conf")

@StartUpProcessor.subscribe(priority=CORE_LAST_PRIORITY - 0) #should go last (even after custom plugins)
def start_api():
    logger.info("Starting up RPC API handler...")
    api.serve_api()
