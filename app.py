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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Updated Socket.IO configuration with ping_timeout and ping_interval
socketio = SocketIO(
    app, 
    async_mode='eventlet', 
    cors_allowed_origins="*",
    ping_timeout=60,  # Increase ping timeout
    ping_interval=25,  # Adjust ping interval
    engineio_logger=True  # Enable engineio logging for debugging
)

# Get Instagram credentials from environment variables
INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "your_username")
INSTAGRAM_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD", "your_password")

# Safe emitter function to avoid bad file descriptor errors
def safe_emit(event, message):
    try:
        socketio.emit(event, message)
        eventlet.sleep(0)  # Give control back to eventlet to process the event
    except Exception as e:
        logger.error(f"Socket emit error: {str(e)}")

# Send mass DM function
def send_mass_dm(target_username, message, delay_between_msgs, max_accounts):
    logger.info(f"Starting mass DM process for target: {target_username}")
    
    # Safer way to emit updates
    safe_emit('update', f"Starting process for {target_username}'s followers")
    
    # Configure Chrome options for Render
    options = webdriver.ChromeOptions()
    options.headless = True
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    try:
        logger.info("Initializing Chrome driver")
        driver = uc.Chrome(options=options)
        safe_emit('update', "Browser initialized")
        
        logger.info("Navigating to Instagram login page")
        driver.get("https://www.instagram.com/accounts/login/")
        safe_emit('update', "Navigated to Instagram login page")
        time.sleep(5)

        try:
            # Log in to Instagram
            logger.info("Attempting to log in")
            username_input = driver.find_element("name", "username")
            password_input = driver.find_element("name", "password")
            
            username_input.send_keys(INSTAGRAM_USERNAME)
            password_input.send_keys(INSTAGRAM_PASSWORD)
            
            login_button = driver.find_element("xpath", "//button[@type='submit']")
            login_button.click()
            safe_emit('update', "Login credentials submitted")
            time.sleep(10)
            
            # Check if login was successful
            if "login" in driver.current_url:
                logger.error("Login failed")
                safe_emit('update', "Login failed - check your credentials")
                return
            
            # Go to target user's followers list
            logger.info(f"Navigating to {target_username}'s following list")
            driver.get(f"https://www.instagram.com/{target_username}/followers/")
            safe_emit('update', f"Navigated to {target_username}'s followers")
            time.sleep(8)
            
            # Extract followers
            logger.info("Extracting followers")
            follower_elements = driver.find_elements("xpath", "//a[@role='link']")
            followers = []
            
            for element in follower_elements:
                username = element.get_attribute("href")
                if username and "//" in username:
                    username = username.split("/")[-2]
                    if username and username != target_username:
                        followers.append(username)
            
            if not followers:
                logger.warning("No followers found")
                safe_emit('update', "No followers found - check the target username")
                return
                
            logger.info(f"Found {len(followers)} followers")
            safe_emit('update', f"Found {len(followers)} followers")
            
            # Limit to max_accounts
            followers = followers[:max_accounts]
            
            count = 0
            for follower in followers:
                try:
                    logger.info(f"Sending message to {follower}")
                    driver.get(f"https://www.instagram.com/direct/new/")
                    time.sleep(3)
                    
                    # Search for user
                    search_input = driver.find_element("xpath", "//input[@placeholder='Search...']")
                    search_input.send_keys(follower)
                    time.sleep(2)
                    
                    # Select user from results
                    user_option = driver.find_element("xpath", f"//div[contains(text(), '{follower}')]")
                    user_option.click()
                    time.sleep(1)
                    
                    # Click Next
                    next_button = driver.find_element("xpath", "//button[contains(text(), 'Next')]")
                    next_button.click()
                    time.sleep(2)
                    
                    # Send message
                    message_input = driver.find_element("xpath", "//textarea[@placeholder='Message...']")
                    message_input.send_keys(message)
                    
                    send_button = driver.find_element("xpath", "//button[contains(text(), 'Send')]")
                    send_button.click()
                    
                    safe_emit('update', f"Sent message to {follower}")
                    count += 1
                    
                    # Add random delay to avoid detection
                    actual_delay = delay_between_msgs + random.uniform(0.5, 2)
                    time.sleep(actual_delay)
                    
                except Exception as e:
                    logger.error(f"Error sending message to {follower}: {str(e)}")
                    safe_emit('update', f"Failed to send message to {follower}: {str(e)}")
            
            safe_emit('update', f"Completed! Sent messages to {count} followers.")
            
        except Exception as e:
            logger.error(f"Error during Instagram automation: {str(e)}")
            safe_emit('update', f"Error: {str(e)}")
    
    except Exception as e:
        logger.error(f"Error initializing Chrome: {str(e)}")
        safe_emit('update', f"Browser initialization error: {str(e)}")
        
    finally:
        try:
            if 'driver' in locals():
                driver.quit()
                logger.info("Driver closed")
        except Exception as e:
            logger.error(f"Error closing driver: {str(e)}")

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

# Handle socket connection events
@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
