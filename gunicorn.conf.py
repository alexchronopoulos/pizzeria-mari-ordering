from __future__ import annotations

import os


bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 30
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True
preload_app = False
worker_tmp_dir = "/dev/shm"
control_socket_disable = True
