import eventlet
eventlet.monkey_patch()

from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO
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
    ping_timeout=20,
    ping_interval=10,
    max_http_buffer_size=1e5,
    async_handlers=True,
    logger=True,
    reconnection=True,
    reconnection_attempts=3,
    reconnection_delay=1000,
)

# Thread lock and stop event
thread_lock = Lock()
stop_event = Event()

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
                proc.kill()
    except Exception as e:
        logger.error(f"Error cleaning up Chrome processes: {e}")

def safe_emit(event, message):
    """Safely emit socket.io events with error handling and rate limiting"""
    with thread_lock:
        try:
            socketio.emit(event, message, namespace='/')
            eventlet.sleep(0.1)
        except Exception as e:
            logger.error(f"Socket emit error: {str(e)}")

def initialize_chrome():
    """Initialize Chrome with minimal memory usage"""
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
    
    try:
        driver = uc.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.implicitly_wait(5)
        return driver
    except Exception as e:
        logger.error(f"Chrome initialization error: {str(e)}")
        return None

def send_mass_dm(target_username, message, delay_between_msgs, max_accounts):
    """Send mass DMs with memory-efficient processing"""
    logger.info(f"Starting mass DM process for target: {target_username}")
    safe_emit('update', f"Starting process for {target_username}'s followers")

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

                username_input = driver.find_element("name", "username")
                password_input = driver.find_element("name", "password")

                username_input.send_keys(INSTAGRAM_USERNAME)
                password_input.send_keys(INSTAGRAM_PASSWORD)
                
                login_button = driver.find_element("xpath", "//button[@type='submit']")
                login_button.click()
                
                eventlet.sleep(3)
                
                if "login" not in driver.current_url:
                    break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception("Failed to login after multiple attempts")
                eventlet.sleep(1)

        safe_emit('update', "Successfully logged in")

        # Get followers with efficient pagination
        driver.get(f"https://www.instagram.com/{target_username}/followers/")
        eventlet.sleep(2)

        followers = []
        scroll_attempts = min(3, max_accounts // 10)
        
        for _ in range(scroll_attempts):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            elements = driver.find_elements("xpath", "//div[@role='dialog']//a[@role='link' and @title]")
            new_followers = [el.get_attribute("title") for el in elements if el.get_attribute("title")]
            followers = list(dict.fromkeys(followers + new_followers))[:max_accounts]
            
            if len(followers) >= max_accounts:
                break
                
            driver.execute_script("document.querySelector('div[role=\"dialog\"]').scrollTo(0, document.querySelector('div[role=\"dialog\"]').scrollHeight)")
            eventlet.sleep(1)

        if not followers:
            safe_emit('update', "No followers found or unable to access follower list")
            return

        safe_emit('update', f"Found {len(followers)} followers to process")

        # Process followers in small batches
        batch_size = 5
        for i in range(0, len(followers), batch_size):
            if stop_event.is_set():
                safe_emit('update', "Process stopped by user")
                return

            batch = followers[i:i + batch_size]
            
            for follower in batch:
                try:
                    if stop_event.is_set():
                        safe_emit('update', "Process stopped by user")
                        return

                    driver.get("https://www.instagram.com/direct/new/")
                    eventlet.sleep(1)

                    search_input = driver.find_element("xpath", "//input[@placeholder='Search...']")
                    search_input.clear()
                    search_input.send_keys(follower)
                    eventlet.sleep(1)

                    user_option = driver.find_element("xpath", f"//div[contains(text(), '{follower}')]")
                    user_option.click()
                    eventlet.sleep(0.5)

                    next_button = driver.find_element("xpath", "//button[contains(text(), 'Next')]")
                    next_button.click()
                    eventlet.sleep(1)

                    message_input = driver.find_element("xpath", "//textarea[@placeholder='Message...']")
                    message_input.send_keys(message)
                    eventlet.sleep(0.5)

                    send_button = driver.find_element("xpath", "//button[contains(text(), 'Send')]")
                    send_button.click()

                    processed_count += 1
                    safe_emit('update', f"✓ Message sent to {follower} ({processed_count}/{len(followers)})")
                    
                    eventlet.sleep(delay_between_msgs)

                except Exception as e:
                    logger.error(f"Error sending message to {follower}: {str(e)}")
                    safe_emit('update', f"× Failed to message {follower}")
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
    socketio.emit('status', {'status': 'connected', 'sid': request.sid})

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
