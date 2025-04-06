import multiprocessing
import os

# Worker Settings
worker_class = "eventlet"
workers = 1  # Keep single worker for WebSocket consistency
threads = 1  # Reduce thread count to prevent conflicts
max_requests = 1000
max_requests_jitter = 100

# Timeout Settings
timeout = 120  # Increased timeout for long-running processes
keepalive = 5  # Increased keepalive

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Server Mechanics
preload_app = True
reload = False
graceful_timeout = 60

# Socket Settings
worker_connections = 1000
backlog = 2048  # Increased for better connection handling

# Memory Management
worker_tmp_dir = "/tmp"
forwarded_allow_ips = "*"

# Resource Management
max_requests_per_child = 1000
worker_max_requests = 1000

def on_starting(server):
    """Clean up any existing processes on startup"""
    os.system("pkill chrome")
    os.system("pkill chromedriver")

def worker_exit(server, worker):
    """Clean up when worker exits"""
    os.system("pkill chrome")
    os.system("pkill chromedriver")
