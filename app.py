import eventlet
eventlet.monkey_patch()

from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO, emit
import time
import os
import logging
import psutil
import signal
from selenium import webdriver
import undetected_chromedriver as uc
from threading import Lock, Event
from dotenv import load_dotenv
import gc
from datetime import datetime
import socket
import dns.resolver
import requests
from requests.adapters import HTTPAdapter, Retry

# Configure DNS resolver with multiple DNS providers for redundancy
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1']
dns.resolver.default_resolver.timeout = 10
dns.resolver.default_resolver.lifetime = 10

# Configure socket timeout and default TCP keepalive
socket.setdefaulttimeout(30)
socket.socket._bind = socket.socket.bind
def _bind_socket_with_keepalive(self, *args, **kwargs):
    self.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    self.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
    self.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
    self.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
    return socket.socket._bind(self, *args, **kwargs)
socket.socket.bind = _bind_socket_with_keepalive

# Configure requests session with retries
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[408, 429, 500, 502, 503, 504]
)
session.mount('http://', HTTPAdapter(max_retries=retries))
session.mount('https://', HTTPAdapter(max_retries=retries))

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
socketio = SocketIO(
    app,
    async_mode='eventlet',
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1e6,
    async_handlers=True,
    logger=True,
    reconnection=True,
    reconnection_attempts=5,
    reconnection_delay=1000,
    reconnection_delay_max=5000,
    engineio_logger=True
)

# Thread lock and stop event
thread_lock = Lock()
stop_event = Event()
active_drivers = set()

# Get Instagram credentials
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
    logger.error("Instagram credentials not set in environment variables.")
    raise ValueError("Instagram credentials must be set in environment variables.")

def cleanup_chrome_processes():
    """Clean up any lingering Chrome processes"""
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            if 'chrome' in proc.info['name'].lower():
                try:
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass
    except Exception as e:
        logger.error(f"Error cleaning up Chrome processes: {e}")

def safe_emit(event, message):
    """Safely emit socket.io events with error handling and rate limiting"""
    try:
        with thread_lock:
            socketio.emit(event, message, namespace='/')
            eventlet.sleep(0.1)
    except Exception as e:
        logger.error(f"Socket emit error: {str(e)}")
        try:
            socketio.emit('error', {'message': 'Communication error occurred'}, namespace='/')
        except:
            pass

def initialize_chrome():
    """Initialize Chrome with optimized settings and DNS configuration"""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=800,600")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--disable-javascript")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-background-networking")
    options.add_argument("--safebrowsing-disable-auto-update")
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--memory-pressure-off")
    options.add_argument("--dns-prefetch-disable")
    options.add_argument("--host-resolver-rules='MAP * 8.8.8.8,1.1.1.1'")
    options.add_argument("--disable-features=NetworkService")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-site-isolation-trials")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-setuid-sandbox")
    
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            # Pre-check DNS resolution
            try:
                socket.gethostbyname('www.instagram.com')
            except socket.gaierror:
                logger.warning("DNS resolution failed, using fallback nameservers")
                dns.resolver.default_resolver.nameservers = ['1.1.1.1', '1.0.0.1']
            
            driver = uc.Chrome(options=options)
            driver.set_page_load_timeout(30)
            driver.implicitly_wait(10)
            
            # Test connection
            driver.get('https://www.instagram.com')
            eventlet.sleep(2)
            
            active_drivers.add(driver)
            return driver
        except Exception as e:
            logger.error(f"Chrome initialization attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                safe_emit('update', f"Browser initialization attempt {attempt + 1} failed, retrying in {retry_delay} seconds...")
                eventlet.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.error("All Chrome initialization attempts failed")
                return None

[Rest of the file remains unchanged from the previous version]
