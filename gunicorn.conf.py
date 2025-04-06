import multiprocessing
import os

# Worker Settings
worker_class = "eventlet"
workers = 1
threads = 2
max_requests = 500
max_requests_jitter = 50

# Timeout Settings
timeout = 60
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Server Mechanics
preload_app = True
reload = False
graceful_timeout = 30

# Socket Settings
worker_connections = 50
backlog = 64

# Memory Management
worker_tmp_dir = "/tmp"
forwarded_allow_ips = "*"

# Resource Limits
max_requests_per_child = 0
worker_max_requests = 1000

def on_starting(server):
    """Clean up any existing Chrome processes on startup"""
    os.system("pkill chrome")
    os.system("pkill chromedriver")
