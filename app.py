import eventlet
eventlet.monkey_patch()  # Ensures compatibility with async operations
from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO
import time
import os
import logging
from selenium import webdriver
import undetected_chromedriver as uc
import random
import gc
from threading import Lock

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Updated Socket.IO configuration with more optimized settings
socketio = SocketIO(
    app, 
    async_mode='eventlet', 
    cors_allowed_origins="*",
    ping_timeout=60,  # Reduced ping timeout
    ping_interval=20,  # Adjusted ping interval
    engineio_logger=True,  # Enable engineio logging for debugging
    max_http_buffer_size=1e6,  # Limit buffer size even further
    async_handlers=True,  # Enable async handlers
    message_queue=None  # Don't use external message queue
)

# Thread lock for safe emitting
thread_lock = Lock()

# Get Instagram credentials from environment variables
INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "your_username")
INSTAGRAM_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD", "your_password")

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
    options.add_argument("--window-size=800,600")  # Further reduced window size
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--blink-settings=imagesEnabled=false")  # Disable images
    options.add_argument("--disable-javascript")  # Disable JS when possible
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-domain-reliability")
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-breakpad")
    options.add_argument("--disk-cache-size=1")
    options.add_argument("--media-cache-size=1")
    options.add_argument("--media-cache-size=1")
    options.add_argument("--memory-model=low")
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,  # Disable images
        "profile.default_content_setting_values.notifications": 2,  # Disable notifications
        "profile.managed_default_content_settings.stylesheets": 2,  # Disable CSS
        "profile.managed_default_content_settings.cookies": 2,  # Disable cookies
        "profile.managed_default_content_settings.javascript": 1,  # Allow JS (needed for Instagram)
        "profile.managed_default_content_settings.plugins": 2,  # Disable plugins
        "profile.managed_default_content_settings.popups": 2,  # Disable popups
        "profile.managed_default_content_settings.geolocation": 2,  # Disable location
        "profile.managed_default_content_settings.media_stream": 2,  # Disable media stream
    })
    
    try:
        return uc.Chrome(options=options)
    except Exception as e:
        logger.error(f"Chrome initialization error: {str(e)}")
        return None

# Send mass DM function - heavily optimized for memory usage
def send_mass_dm(target_username, message, delay_between_msgs, max_accounts):
    logger.info(f"Starting mass DM process for target: {target_username}")
    safe_emit('update', f"Starting process for {target_username}'s followers")
    
    driver = None
    followers = []
    processed_count = 0
    
    try:
        # Initialize Chrome with minimal settings
        safe_emit('update', "Initializing browser (this may take a moment)...")
        driver = initialize_chrome()
        
        if not driver:
            safe_emit('update', "Failed to initialize browser - please try again later")
            return
        
        safe_emit('update', "Browser initialized")
        
        # Set page load timeout
        driver.set_page_load_timeout(30)
        
        # Login to Instagram with error handling
        try:
            logger.info("Navigating to Instagram login page")
            driver.get("https://www.instagram.com/accounts/login/")
            safe_emit('update', "Navigated to Instagram login page")
            
            # Yield control to prevent timeouts
            eventlet.sleep(2)
            
            # Simple wait function that yields control
            def wait_with_yield(seconds):
                steps = min(seconds, 10)  # Max 10 steps
                for _ in range(steps):
                    eventlet.sleep(seconds/steps)
            
            # Check if login page loaded
            if "Instagram" not in driver.title:
                safe_emit('update', "Instagram login page did not load properly")
                return
            
            logger.info("Attempting to log in")
            username_input = driver.find_element("name", "username")
            password_input = driver.find_element("name", "password")
            
            # Type with delays to appear more human-like
            for char in INSTAGRAM_USERNAME:
                username_input.send_keys(char)
                time.sleep(random.uniform(0.05, 0.1))
                
            wait_with_yield(1)
            
            for char in INSTAGRAM_PASSWORD:
                password_input.send_keys(char)
                time.sleep(random.uniform(0.05, 0.1))
            
            wait_with_yield(1)
            
            login_button = driver.find_element("xpath", "//button[@type='submit']")
            login_button.click()
            safe_emit('update', "Login credentials submitted")
            
            # Yield control and wait in smaller chunks
            for i in range(5):
                eventlet.sleep(1)
                # Force garbage collection during waiting periods
                if i % 2 == 0:
                    gc.collect()
            
            # Check if login was successful
            if "login" in driver.current_url:
                logger.error("Login failed")
                safe_emit('update', "Login failed - check your credentials")
                return
                
            safe_emit('update', "Successfully logged in")
            
            # Clear cookies and storage periodically to save memory
            driver.execute_script("window.localStorage.clear();")
            driver.execute_script("window.sessionStorage.clear();")
            
            # Go to target user's profile first to verify existence
            logger.info(f"Verifying {target_username}'s profile")
            driver.get(f"https://www.instagram.com/{target_username}/")
            wait_with_yield(3)
            
            # Check if profile exists
            if "Page Not Found" in driver.title or "Sorry, this page isn't available." in driver.page_source:
                safe_emit('update', f"User {target_username} not found - please check the username")
                return
                
            # Go to target user's followers list
            logger.info(f"Navigating to {target_username}'s followers list")
            driver.get(f"https://www.instagram.com/{target_username}/followers/")
            safe_emit('update', f"Navigated to {target_username}'s followers")
            wait_with_yield(4)
            
            # Extract followers - only a subset to save memory
            logger.info("Extracting followers")
            
            # More conservative approach: extract only visible followers first
            try:
                follower_divs = driver.find_elements("xpath", "//div[@role='dialog']//a[@role='link' and @title]")
                
                # If we couldn't find followers with that xpath, try another common pattern
                if not follower_divs:
                    follower_divs = driver.find_elements("xpath", "//div[@role='dialog']//a[contains(@href, '/')]")
                
                # Extract usernames, with frequent garbage collection
                batch_size = 5
                for i, element in enumerate(follower_divs):
                    try:
                        if element.get_attribute("title"):
                            followers.append(element.get_attribute("title"))
                        elif element.get_attribute("href"):
                            href = element.get_attribute("href")
                            if "//" in href:
                                username = href.split("/")[-2]
                                if username and username != target_username:
                                    followers.append(username)
                                    
                        # Periodically yield control and collect garbage
                        if i % batch_size == 0:
                            eventlet.sleep(0.1)
                            gc.collect()
                            
                    except Exception as e:
                        logger.warning(f"Error extracting follower: {str(e)}")
                        continue
                        
                # Remove duplicates while preserving order
                followers = list(dict.fromkeys(followers))
                
            except Exception as e:
                logger.error(f"Error extracting followers: {str(e)}")
                safe_emit('update', f"Error extracting followers: {str(e)}")
            
            # Clear element references to free memory
            del follower_divs
            gc.collect()
            
            if not followers:
                logger.warning("No followers found")
                safe_emit('update', "No followers found or unable to access follower list")
                return
                
            logger.info(f"Found {len(followers)} followers")
            safe_emit('update', f"Found {len(followers)} followers")
            
            # Limit to max_accounts (use even lower limit than requested for memory saving)
            actual_max = min(max_accounts, 20)  # Hard cap at 20 for Render
            followers = followers[:actual_max]
            
            # Process followers in smaller batches
            batch_size = 5
            batches = [followers[i:i+batch_size] for i in range(0, len(followers), batch_size)]
            
            for batch_index, batch in enumerate(batches):
                safe_emit('update', f"Processing batch {batch_index+1}/{len(batches)}")
                
                for follower in batch:
                    try:
                        logger.info(f"Sending message to {follower}")
                        safe_emit('update', f"Preparing to message {follower}")
                        
                        # Navigate to direct message page
                        driver.get(f"https://www.instagram.com/direct/new/")
                        wait_with_yield(2)
                        
                        # Search for user
                        try:
                            search_input = driver.find_element("xpath", "//input[@placeholder='Search...']")
                            
                            # Type with delays
                            for char in follower:
                                search_input.send_keys(char)
                                time.sleep(random.uniform(0.05, 0.1))
                                
                            eventlet.sleep(1)
                            
                            # Select user from results
                            user_options = driver.find_elements("xpath", f"//div[contains(text(), '{follower}')]")
                            
                            if user_options:
                                user_options[0].click()
                                eventlet.sleep(1)
                                
                                # Click Next
                                next_button = driver.find_element("xpath", "//button[contains(text(), 'Next')]")
                                next_button.click()
                                wait_with_yield(1)
                                
                                # Send message
                                message_input = driver.find_element("xpath", "//textarea[@placeholder='Message...']")
                                
                                # Type with delays
                                for char in message:
                                    message_input.send_keys(char)
                                    time.sleep(random.uniform(0.01, 0.03))
                                
                                wait_with_yield(0.5)
                                
                                send_button = driver.find_element("xpath", "//button[contains(text(), 'Send')]")
                                send_button.click()
                                
                                safe_emit('update', f"✓ Sent message to {follower}")
                                processed_count += 1
                                
                                # Clear browser data to save memory
                                driver.execute_script("window.localStorage.clear();")
                                driver.execute_script("window.sessionStorage.clear();")
                                
                                # Force garbage collection
                                gc.collect()
                                
                                # Add random delay to avoid detection
                                actual_delay = delay_between_msgs + random.uniform(1, 3)
                                
                                # Wait in chunks to allow yielding control
                                chunks = 5
                                for _ in range(chunks):
                                    eventlet.sleep(actual_delay / chunks)
                                
                            else:
                                safe_emit('update', f"× Couldn't find user {follower} in search results")
                                
                        except Exception as e:
                            logger.error(f"Error in DM workflow for {follower}: {str(e)}")
                            safe_emit('update', f"× Error sending to {follower}")
                        
                    except Exception as e:
                        logger.error(f"Error sending message to {follower}: {str(e)}")
                        safe_emit('update', f"× Failed to message {follower}: {str(e)[:50]}")
                    
                    # Yield control to eventlet
                    eventlet.sleep(0.5)
                
                # Between batches, restart the browser to free memory
                if batch_index < len(batches) - 1:
                    safe_emit('update', "Refreshing browser to free memory...")
                    
                    # Quit the current driver
                    try:
                        driver.quit()
                        del driver
                        gc.collect()
                    except:
                        pass
                    
                    # Wait a moment
                    eventlet.sleep(2)
                    
                    # Restart the driver
                    driver = initialize_chrome()
                    
                    if not driver:
                        safe_emit('update', "Failed to reinitialize browser - stopping process")
                        return
                    
                    # Login again
                    try:
                        driver.get("https://www.instagram.com/accounts/login/")
                        wait_with_yield(3)
                        
                        username_input = driver.find_element("name", "username")
                        password_input = driver.find_element("name", "password")
                        
                        username_input.send_keys(INSTAGRAM_USERNAME)
                        password_input.send_keys(INSTAGRAM_PASSWORD)
                        
                        login_button = driver.find_element("xpath", "//button[@type='submit']")
                        login_button.click()
                        
                        wait_with_yield(5)
                        
                        if "login" in driver.current_url:
                            safe_emit('update', "Failed to log back in - stopping process")
                            return
                            
                        safe_emit('update', "Successfully logged back in, continuing...")
                        
                    except Exception as e:
                        safe_emit('update', f"Error logging back in: {str(e)[:50]}")
                        return
            
            safe_emit('update', f"Completed! Sent messages to {processed_count}/{len(followers)} followers.")
            
        except Exception as e:
            logger.error(f"Error during Instagram automation: {str(e)}")
            safe_emit('update', f"Error: {str(e)[:100]}")
    
    except Exception as e:
        logger.error(f"Critical error: {str(e)}")
        safe_emit('update', f"Critical error: {str(e)[:100]}")
        
    finally:
        try:
            if driver:
                driver.quit()
                logger.info("Driver closed")
                del driver
                gc.collect()  # Force garbage collection
        except Exception as e:
            logger.error(f"Error closing driver: {str(e)}")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            target_username = request.form["username"]
            message = request.form["message"]
            delay_between_msgs = int(request.form["delay_between_msgs"])
            max_accounts = int(request.form["max_accounts"])
            
            # Enforce limits for memory protection
            delay_between_msgs = max(delay_between_msgs, 20)  # Minimum 20 sec delay
            max_accounts = min(max_accounts, 20)  # Maximum 20 accounts
            
            # Start background task safely
            socketio.start_background_task(
                send_mass_dm, 
                target_username, 
                message, 
                delay_between_msgs, 
                max_accounts
            )
            return render_template("index.html", started=True)
        except Exception as e:
            logger.error(f"Error in form submission: {str(e)}")
            return render_template("index.html", error=str(e))
    
    return render_template("index.html", started=False)

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "memory_usage": get_memory_usage()}), 200

# Simple memory usage reporting
def get_memory_usage():
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return {
            "percent": process.memory_percent(),
            "mb": process.memory_info().rss / 1024 / 1024
        }
    except:
        return {"error": "Unable to get memory info"}

# Handle socket connection events
@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    socketio.emit('status', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")

if __name__ == "__main__":
    # Use lower worker connections and shorter timeout
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
