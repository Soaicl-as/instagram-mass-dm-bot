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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException

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
    engineio_logger=True,
    manage_session=False,
    websocket_max_wait=60
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
    options.add_argument("--window-size=1920,1080")  # Increased window size
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
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
            
            # Test connection with retry
            for _ in range(3):
                try:
                    driver.get('https://www.instagram.com')
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                    break
                except Exception:
                    eventlet.sleep(2)
                    continue
            
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

def get_user_list(driver, target_username, extract_type, max_accounts):
    """Get list of followers or following based on extract_type"""
    try:
        # Navigate to appropriate page
        url = f"https://www.instagram.com/{target_username}/{'followers' if extract_type == 'followers' else 'following'}/"
        driver.get(url)
        eventlet.sleep(3)

        users = []
        scroll_attempts = min(3, max_accounts // 10)
        
        # Wait for dialog to appear
        dialog_xpath = "//div[@role='dialog']"
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, dialog_xpath))
        )
        
        for _ in range(scroll_attempts):
            if stop_event.is_set():
                return users

            elements = driver.find_elements("xpath", "//div[@role='dialog']//a[@role='link' and @title]")
            new_users = [el.get_attribute("title") for el in elements if el.get_attribute("title")]
            users = list(dict.fromkeys(users + new_users))[:max_accounts]
            
            if len(users) >= max_accounts:
                break
                
            driver.execute_script(
                "document.querySelector('div[role=\"dialog\"]').scrollTo(0, document.querySelector('div[role=\"dialog\"]').scrollHeight)"
            )
            eventlet.sleep(1.5)

        return users[:max_accounts]
    except Exception as e:
        logger.error(f"Error getting user list: {str(e)}")
        return []

def send_mass_dm(target_username, message, delay_between_msgs, max_accounts, extract_type):
    """Send mass DMs with improved error handling"""
    logger.info(f"Starting mass DM process for target: {target_username}")
    safe_emit('update', f"Starting process for {target_username}'s {extract_type}")

    driver = None
    processed_count = 0
    max_retries = 2
    stop_event.clear()

    try:
        driver = initialize_chrome()
        if not driver:
            safe_emit('update', "Failed to initialize browser - please try again")
            return

        safe_emit('update', "Browser initialized successfully")

        # Login with retry mechanism
        for attempt in range(max_retries):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            try:
                driver.get("https://www.instagram.com/accounts/login/")
                eventlet.sleep(2)

                # Wait for login form
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "username"))
                )

                username_input = driver.find_element("name", "username")
                password_input = driver.find_element("name", "password")

                username_input.send_keys(INSTAGRAM_USERNAME)
                password_input.send_keys(INSTAGRAM_PASSWORD)
                
                login_button = driver.find_element("xpath", "//button[@type='submit']")
                login_button.click()
                
                eventlet.sleep(3)
                
                # Check if login was successful
                if "login" not in driver.current_url:
                    break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception("Failed to login after multiple attempts")
                eventlet.sleep(2)

        safe_emit('update', "Successfully logged in")

        # Get users list based on extract_type
        users = get_user_list(driver, target_username, extract_type, max_accounts)

        if not users:
            safe_emit('update', f"No {extract_type} found or unable to access list")
            return

        safe_emit('update', f"Found {len(users)} {extract_type} to process")

        # Process users in small batches
        batch_size = 5
        for i in range(0, len(users), batch_size):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            batch = users[i:i + batch_size]
            
            for user in batch:
                try:
                    if stop_event.is_set():
                        safe_emit('update', "Process stopped by user")
                        return

                    driver.get("https://www.instagram.com/direct/new/")
                    eventlet.sleep(1.5)

                    # Wait for search input
                    search_input = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search...']"))
                    )
                    search_input.clear()
                    search_input.send_keys(user)
                    eventlet.sleep(1.5)

                    # Wait for user option to appear
                    user_option = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, f"//div[contains(text(), '{user}')]"))
                    )
                    user_option.click()
                    eventlet.sleep(1)

                    next_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Next')]"))
                    )
                    next_button.click()
                    eventlet.sleep(1.5)

                    message_input = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, "//textarea[@placeholder='Message...']"))
                    )
                    message_input.send_keys(message)
                    eventlet.sleep(1)

                    send_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Send')]"))
                    )
                    send_button.click()

                    processed_count += 1
                    safe_emit('update', f"✓ Message sent to {user} ({processed_count}/{len(users)})")
                    
                    eventlet.sleep(delay_between_msgs)

                except TimeoutException:
                    logger.error(f"Timeout while processing {user}")
                    safe_emit('update', f"× Timeout while messaging {user}")
                    eventlet.sleep(1)
                except Exception as e:
                    logger.error(f"Error sending message to {user}: {str(e)}")
                    safe_emit('update', f"× Failed to message {user}")
                    eventlet.sleep(1)

            # Memory management between batches
            driver.execute_script("window.gc();")
            gc.collect()
            eventlet.sleep(2)

    except Exception as e:
        logger.error(f"Critical error in mass DM process: {str(e)}")
        safe_emit('update', f"Critical error: {str(e)[:100]}")

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
            logger.info("Chrome driver closed")
        
        cleanup_chrome_processes()
        gc.collect()
        
        if stop_event.is_set():
            safe_emit('update', "Process stopped by user")
        else:
            safe_emit('update', f"Process completed. Successfully sent {processed_count} messages.")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            target_username = request.form["username"].strip()
            message = request.form["message"].strip()
            delay_between_msgs = max(int(request.form["delay_between_msgs"]), 45)
            max_accounts = min(int(request.form["max_accounts"]), 30)
            extract_type = request.form["extract_type"]  # 'followers' or 'following'

            socketio.start_background_task(
                send_mass_dm,
                target_username,
                message,
                delay_between_msgs,
                max_accounts,
                extract_type
            )
            return render_template("index.html", started=True)
        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            return render_template("index.html", error=str(e))

    return render_template("index.html", started=False)

@socketio.on('stop_process')
def handle_stop_process():
    """Handle stop process request from client"""
    stop_event.set()
    logger.info("Stop process requested by user")

@socketio.on('ping')
def handle_ping():
    """Handle ping from client to keep connection alive"""
    emit('pong')

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "memory_usage": psutil.Process().memory_info().rss / 1024 / 1024
    }), 200

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    socketio.emit('status', {
        'status': 'connected',
        'sid': request.sid,
        'timestamp': datetime.now().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False
    )
