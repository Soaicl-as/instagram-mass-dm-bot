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

# Configure DNS resolver with Google's DNS
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']

# Increase socket timeout
socket.setdefaulttimeout(30)

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
    options.add_argument("--dns-prefetch-disable")  # Disable DNS prefetching
    options.add_argument("--host-resolver-rules='MAP * 8.8.8.8'")  # Force DNS resolution through Google DNS
    
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            driver = uc.Chrome(options=options)
            driver.set_page_load_timeout(30)
            driver.implicitly_wait(10)
            active_drivers.add(driver)
            return driver
        except Exception as e:
            logger.error(f"Chrome initialization attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                safe_emit('update', f"Browser initialization attempt {attempt + 1} failed, retrying...")
                eventlet.sleep(retry_delay)
            else:
                logger.error("All Chrome initialization attempts failed")
                return None

def send_mass_dm(target_username, message, delay_between_msgs, max_accounts):
    """Send mass DMs with improved error handling and memory management"""
    logger.info(f"Starting mass DM process for target: {target_username}")
    safe_emit('update', f"Starting process for {target_username}'s followers")

    driver = None
    processed_count = 0
    max_retries = 3
    stop_event.clear()

    try:
        # Initialize Chrome with retry mechanism
        for attempt in range(max_retries):
            driver = initialize_chrome()
            if driver:
                break
            if attempt < max_retries - 1:
                safe_emit('update', f"Retrying browser initialization (attempt {attempt + 2}/{max_retries})...")
                eventlet.sleep(5)
            else:
                safe_emit('update', "Failed to initialize browser after multiple attempts - please try again later")
                return

        safe_emit('update', "Browser initialized successfully")

        # Login with enhanced retry mechanism
        login_successful = False
        for attempt in range(max_retries):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            try:
                driver.get("https://www.instagram.com/accounts/login/")
                eventlet.sleep(3)

                username_input = driver.find_element("name", "username")
                password_input = driver.find_element("name", "password")

                username_input.send_keys(INSTAGRAM_USERNAME)
                password_input.send_keys(INSTAGRAM_PASSWORD)
                
                login_button = driver.find_element("xpath", "//button[@type='submit']")
                login_button.click()
                
                eventlet.sleep(5)
                
                if "login" not in driver.current_url:
                    login_successful = True
                    break
                
                eventlet.sleep(2 ** attempt)  # Exponential backoff
            except Exception as e:
                logger.error(f"Login attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    safe_emit('update', f"Login attempt failed, retrying... ({attempt + 1}/{max_retries})")
                    eventlet.sleep(2 ** attempt)
                else:
                    raise Exception("Failed to login after multiple attempts")

        if not login_successful:
            raise Exception("Failed to login after multiple attempts")

        safe_emit('update', "Successfully logged in")

        # Get followers with improved pagination
        driver.get(f"https://www.instagram.com/{target_username}/followers/")
        eventlet.sleep(3)

        followers = []
        scroll_attempts = min(5, max_accounts // 8)
        last_height = 0
        
        for attempt in range(scroll_attempts):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            elements = driver.find_elements("xpath", "//div[@role='dialog']//a[@role='link' and @title]")
            new_followers = [el.get_attribute("title") for el in elements if el.get_attribute("title")]
            followers = list(dict.fromkeys(followers + new_followers))[:max_accounts]
            
            if len(followers) >= max_accounts:
                break
                
            dialog = driver.find_element("xpath", "//div[@role='dialog']")
            driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight)", dialog)
            eventlet.sleep(2)

            new_height = driver.execute_script("return arguments[0].scrollHeight", dialog)
            if new_height == last_height:
                break
            last_height = new_height

        if not followers:
            safe_emit('update', "No followers found or unable to access follower list")
            return

        safe_emit('update', f"Found {len(followers)} followers to process")

        # Process followers with improved batching
        batch_size = 3
        for i in range(0, len(followers), batch_size):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            batch = followers[i:i + batch_size]
            
            for follower in batch:
                if stop_event.is_set():
                    safe_emit('update', "Process stopped by user")
                    return

                try:
                    driver.get("https://www.instagram.com/direct/new/")
                    eventlet.sleep(2)

                    search_input = driver.find_element("xpath", "//input[@placeholder='Search...']")
                    search_input.clear()
                    search_input.send_keys(follower)
                    eventlet.sleep(2)

                    user_option = driver.find_element("xpath", f"//div[contains(text(), '{follower}')]")
                    user_option.click()
                    eventlet.sleep(1)

                    next_button = driver.find_element("xpath", "//button[contains(text(), 'Next')]")
                    next_button.click()
                    eventlet.sleep(2)

                    message_input = driver.find_element("xpath", "//textarea[@placeholder='Message...']")
                    message_input.send_keys(message)
                    eventlet.sleep(1)

                    send_button = driver.find_element("xpath", "//button[contains(text(), 'Send')]")
                    send_button.click()

                    processed_count += 1
                    safe_emit('update', f"✓ Message sent to {follower} ({processed_count}/{len(followers)})")
                    
                    eventlet.sleep(delay_between_msgs)

                except Exception as e:
                    logger.error(f"Error sending message to {follower}: {str(e)}")
                    safe_emit('update', f"× Failed to message {follower}")
                    eventlet.sleep(2)

            # Memory management
            driver.execute_script("window.gc();")
            gc.collect()
            eventlet.sleep(3)

    except Exception as e:
        logger.error(f"Critical error in mass DM process: {str(e)}")
        safe_emit('update', f"Critical error: {str(e)[:100]}")

    finally:
        if driver:
            try:
                driver.quit()
                active_drivers.remove(driver)
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

            socketio.start_background_task(
                send_mass_dm,
                target_username,
                message,
                delay_between_msgs,
                max_accounts
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
    cleanup_chrome_processes()

@app.route("/health", methods=["GET"])
def health_check():
    """Enhanced health check endpoint"""
    memory = psutil.Process().memory_info()
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "memory": {
            "rss": memory.rss / 1024 / 1024,
            "vms": memory.vms / 1024 / 1024
        },
        "active_drivers": len(active_drivers),
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent
    }), 200

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info(f"Client connected: {request.sid}")
    socketio.emit('status', {
        'status': 'connected',
        'sid': request.sid,
        'timestamp': datetime.utcnow().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info(f"Client disconnected: {request.sid}")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}")
    cleanup_chrome_processes()
    os._exit(0)

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False
    )
