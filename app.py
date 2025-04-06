import eventlet
eventlet.monkey_patch()

from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO
import time
import os
import logging
from selenium import webdriver
import undetected_chromedriver as uc
from threading import Lock
from dotenv import load_dotenv

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
    engineio_logger=True,
    max_http_buffer_size=1e6,
    async_handlers=True,
    logger=True,
    reconnection=True,
    reconnection_attempts=5,
    reconnection_delay=1000,
)

# Thread lock for safe emitting
thread_lock = Lock()

# Get Instagram credentials from environment variables
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
    logger.error("Instagram credentials not set in environment variables.")
    raise ValueError("Instagram credentials must be set in environment variables.")

def safe_emit(event, message):
    """Safely emit socket.io events with error handling and rate limiting"""
    with thread_lock:
        try:
            socketio.emit(event, message, namespace='/')
            eventlet.sleep(0.1)  # Rate limiting
        except Exception as e:
            logger.error(f"Socket emit error: {str(e)}")

def initialize_chrome():
    """Initialize Chrome with optimized settings"""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    
    # Performance optimizations
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-setuid-sandbox")
    
    try:
        driver = uc.Chrome(options=options)
        driver.set_page_load_timeout(30)
        driver.implicitly_wait(10)
        return driver
    except Exception as e:
        logger.error(f"Chrome initialization error: {str(e)}")
        return None

def send_mass_dm(target_username, message, delay_between_msgs, max_accounts):
    """Send mass DMs with improved error handling and rate limiting"""
    logger.info(f"Starting mass DM process for target: {target_username}")
    safe_emit('update', f"Starting process for {target_username}'s followers")

    driver = None
    processed_count = 0
    max_retries = 3

    try:
        driver = initialize_chrome()
        if not driver:
            safe_emit('update', "Failed to initialize browser - please try again")
            return

        safe_emit('update', "Browser initialized successfully")

        # Login with retry mechanism
        for attempt in range(max_retries):
            try:
                driver.get("https://www.instagram.com/accounts/login/")
                eventlet.sleep(3)  # Wait for page load

                username_input = driver.find_element("name", "username")
                password_input = driver.find_element("name", "password")

                username_input.send_keys(INSTAGRAM_USERNAME)
                password_input.send_keys(INSTAGRAM_PASSWORD)
                
                login_button = driver.find_element("xpath", "//button[@type='submit']")
                login_button.click()
                
                eventlet.sleep(5)  # Wait for login
                
                if "login" not in driver.current_url:
                    break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception("Failed to login after multiple attempts")
                eventlet.sleep(2)

        safe_emit('update', "Successfully logged in")

        # Get followers with pagination
        driver.get(f"https://www.instagram.com/{target_username}/followers/")
        eventlet.sleep(4)

        followers = []
        last_height = driver.execute_script("return document.querySelector('div[role=\"dialog\"]').scrollHeight")
        
        while len(followers) < max_accounts:
            # Scroll followers dialog
            driver.execute_script("document.querySelector('div[role=\"dialog\"]').scrollTo(0, document.querySelector('div[role=\"dialog\"]').scrollHeight)")
            eventlet.sleep(2)
            
            # Get followers
            elements = driver.find_elements("xpath", "//div[@role='dialog']//a[@role='link' and @title]")
            new_followers = [el.get_attribute("title") for el in elements if el.get_attribute("title")]
            followers = list(dict.fromkeys(followers + new_followers))  # Remove duplicates
            
            new_height = driver.execute_script("return document.querySelector('div[role=\"dialog\"]').scrollHeight")
            if new_height == last_height or len(followers) >= max_accounts:
                break
            last_height = new_height

        if not followers:
            safe_emit('update', "No followers found or unable to access follower list")
            return

        followers = followers[:max_accounts]
        safe_emit('update', f"Found {len(followers)} followers to process")

        # Process followers with rate limiting
        for follower in followers:
            try:
                driver.get("https://www.instagram.com/direct/new/")
                eventlet.sleep(2)

                # Search and select user
                search_input = driver.find_element("xpath", "//input[@placeholder='Search...']")
                search_input.clear()
                search_input.send_keys(follower)
                eventlet.sleep(1.5)

                user_option = driver.find_element("xpath", f"//div[contains(text(), '{follower}')]")
                user_option.click()
                eventlet.sleep(1)

                # Send message
                next_button = driver.find_element("xpath", "//button[contains(text(), 'Next')]")
                next_button.click()
                eventlet.sleep(1.5)

                message_input = driver.find_element("xpath", "//textarea[@placeholder='Message...']")
                message_input.send_keys(message)
                eventlet.sleep(1)

                send_button = driver.find_element("xpath", "//button[contains(text(), 'Send')]")
                send_button.click()

                processed_count += 1
                safe_emit('update', f"✓ Message sent to {follower} ({processed_count}/{len(followers)})")
                
                # Dynamic delay between messages
                delay = delay_between_msgs + (delay_between_msgs * 0.2 * (processed_count % 3))
                eventlet.sleep(delay)

            except Exception as e:
                logger.error(f"Error sending message to {follower}: {str(e)}")
                safe_emit('update', f"× Failed to message {follower}")
                eventlet.sleep(delay_between_msgs)  # Still wait before next attempt

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
        
        safe_emit('update', f"Process completed. Successfully sent {processed_count} messages.")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            target_username = request.form["username"].strip()
            message = request.form["message"].strip()
            delay_between_msgs = max(int(request.form["delay_between_msgs"]), 30)  # Minimum 30s delay
            max_accounts = min(int(request.form["max_accounts"]), 100)  # Maximum 100 accounts

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

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200

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
