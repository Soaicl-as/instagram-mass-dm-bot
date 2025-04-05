import eventlet
eventlet.monkey_patch()  # Ensures compatibility with async operations
from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO
import time
import os
import logging
from selenium import webdriver
import undetected_chromedriver as uc
from threading import Lock

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
socketio = SocketIO(
    app,
    async_mode='eventlet',
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=20,
    engineio_logger=True,
    max_http_buffer_size=1e6,
    async_handlers=True,
)

# Thread lock for safe emitting
thread_lock = Lock()

# Get Instagram credentials from environment variables
INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD")

if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
    logger.error("Instagram credentials not set in environment variables.")
    raise ValueError("Instagram credentials must be set in environment variables.")

# Safe emitter function to avoid bad file descriptor errors
def safe_emit(event, message):
    with thread_lock:
        try:
            socketio.emit(event, message)
            eventlet.sleep(0.1)  # Give control back to eventlet to process the event
        except Exception as e:
            logger.error(f"Socket emit error: {str(e)}")

# Function to initialize Chrome with minimal footprint
def initialize_chrome():
    options = webdriver.ChromeOptions()
    options.headless = True
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
    options.add_argument("--disable-domain-reliability")
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-breakpad")
    options.add_argument("--disk-cache-size=1")
    options.add_argument("--media-cache-size=1")
    options.add_argument("--memory-model=low")
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_setting_values.cookies": 2,
        "profile.managed_default_content_settings.javascript": 1,
        "profile.managed_default_content_settings.plugins": 2,
        "profile.managed_default_content_settings.popups": 2,
        "profile.managed_default_content_settings.geolocation": 2,
        "profile.managed_default_content_settings.media_stream": 2,
    })

    try:
        return uc.Chrome(options=options)
    except Exception as e:
        logger.error(f"Chrome initialization error: {str(e)}")
        return None

# Send mass DM function
def send_mass_dm(target_username, message, delay_between_msgs, max_accounts):
    logger.info(f"Starting mass DM process for target: {target_username}")
    safe_emit('update', f"Starting process for {target_username}'s followers")

    driver = None
    followers = []
    processed_count = 0

    try:
        driver = initialize_chrome()
        if not driver:
            safe_emit('update', "Failed to initialize browser - please try again later")
            return

        safe_emit('update', "Browser initialized")

        # Login to Instagram
        driver.get("https://www.instagram.com/accounts/login/")
        time.sleep(2)  # Wait for the page to load

        username_input = driver.find_element("name", "username")
        password_input = driver.find_element("name", "password")

        username_input.send_keys(INSTAGRAM_USERNAME)
        password_input.send_keys(INSTAGRAM_PASSWORD)

        login_button = driver.find_element("xpath", "//button[@type='submit']")
        login_button.click()

        time.sleep(5)  # Wait for login to complete

        if "login" in driver.current_url:
            safe_emit('update', "Login failed - check your credentials")
            return

        safe_emit('update', "Successfully logged in")

        # Navigate to target user's followers
        driver.get(f"https://www.instagram.com/{target_username}/followers/")
        time.sleep(4)  # Wait for the followers page to load

        # Extract followers
        follower_divs = driver.find_elements("xpath", "//div[@role='dialog']//a[@role='link' and @title]")
        followers = [element.get_attribute("title") for element in follower_divs if element.get_attribute("title")]

        if not followers:
            safe_emit('update', "No followers found or unable to access follower list")
            return

        logger.info(f"Found {len(followers)} followers")
        safe_emit('update', f"Found {len(followers)} followers")

        # Process followers
        for follower in followers[:max_accounts]:
            try:
                # Navigate to direct message page
                driver.get(f"https://www.instagram.com/direct/new/")
                time.sleep(2)

                search_input = driver.find_element("xpath", "//input[@placeholder='Search...']")
                search_input.send_keys(follower)
                time.sleep(1)

                user_options = driver.find_elements("xpath", f"//div[contains(text(), '{follower}')]")
                if user_options:
                    user_options[0].click()
                    time.sleep(1)

                    next_button = driver.find_element("xpath", "//button[contains(text(), 'Next')]")
                    next_button.click()
                    time.sleep(1)

                    message_input = driver.find_element("xpath", "//textarea[@placeholder='Message...']")
                    message_input.send_keys(message)
                    time.sleep(1)

                    send_button = driver.find_element("xpath", "//button[contains(text(), 'Send')]")
                    send_button.click()

                    safe_emit('update', f"✓ Sent message to {follower}")
                    processed_count += 1

                    time.sleep(delay_between_msgs)  # Delay between messages
                else:
                    safe_emit('update', f"× Couldn't find user {follower} in search results")

            except Exception as e:
                logger.error(f"Error sending message to {follower}: {str(e)}")
                safe_emit('update', f"× Failed to message {follower}: {str(e)[:50]}")

    except Exception as e:
        logger.error(f"Critical error: {str(e)}")
        safe_emit('update', f"Critical error: {str(e)[:100]}")

    finally:
        if driver:
            driver.quit()
            logger.info("Driver closed")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        target_username = request.form["username"]
        message = request.form["message"]
        delay_between_msgs = int(request.form["delay_between_msgs"])
        max_accounts = int(request.form["max_accounts"])

        # Start background task safely
        socketio.start_background_task(
            send_mass_dm,
            target_username,
            message,
            delay_between_msgs,
            max_accounts
        )
        return render_template("index.html", started=True)

    return render_template("index.html", started=False)

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    socketio.emit('status', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
