# OpenFaaS wrapper for SeBS benchmarks (python3-http / python3-http-debian templates).
# Adapted from benchmarks/wrappers/openwhisk/python/__main__.py in the SeBS repository.
import datetime
import json
import socket
import threading
import traceback

from .function import handler as benchmark_handler   # the unmodified SeBS benchmark (function.py)

_lock = threading.Lock()
_first_request = True


def _is_cold():
    """True only for the first request served by this process (= this container)."""
    global _first_request
    with _lock:
        cold, _first_request = _first_request, False
    return cold


def handle(event, context):
    begin = datetime.datetime.now()
    is_cold = _is_cold()
    status = 200
    try:
        inputs = json.loads(event.body) if event.body else {}
        ret = benchmark_handler(inputs)
        log_data = {"result": ret["result"]}
        if "measurement" in ret:
            log_data["measurement"] = ret["measurement"]
    except Exception as e:                                   # report failures instead of crashing
        status = 500
        log_data = {"result": f"Error - invocation failed! Reason: {e}",
                    "trace": traceback.format_exc()}
    end = datetime.datetime.now()

    body = {
        "begin": begin.timestamp(),                          # epoch seconds (float)
        "end": end.timestamp(),
        "results_time": (end - begin) / datetime.timedelta(microseconds=1),   # microseconds, as in SeBS
        "is_cold": is_cold,
        "container_id": socket.gethostname(),
        "request_id": event.headers.get("X-Call-Id") if event.headers else None,
        "result": log_data,
    }
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }