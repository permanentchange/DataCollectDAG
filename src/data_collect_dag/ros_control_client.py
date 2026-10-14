from __future__ import annotations

from http.client import HTTPConnection
import json
import os
import threading
import time
from typing import Any, Dict, Optional
from xmlrpc.client import ServerProxy, Transport

from data_collect_dag.ros_env import ensure_ros_python_path


DEFAULT_CONTROL_TOPIC = "/data_collect/control"
DEFAULT_STATUS_TOPIC = "/data_collect/status"
DEFAULT_TIMEOUT_SEC = 5.0


class _TimeoutTransport(Transport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: str) -> HTTPConnection:
        return HTTPConnection(host, timeout=self.timeout)


def wait_for_master(timeout: float) -> None:
    uri = os.environ.get("ROS_MASTER_URI") or "http://localhost:11311"
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"ROS Master unavailable at {uri} within {timeout:g} seconds: {last_error}")
        try:
            with ServerProxy(uri, transport=_TimeoutTransport(min(0.5, remaining))) as proxy:
                code, message, _pid = proxy.getPid("/data_collect_dag_cli")
                if code != 1:
                    raise RuntimeError(message)
            return
        except Exception as exc:
            last_error = exc
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _import_ros():
    ensure_ros_python_path()
    import rospy  # type: ignore
    from std_msgs.msg import String  # type: ignore

    return rospy, String


def format_status(snapshot: Dict[str, Any]) -> str:
    free = snapshot.get("disk_free_gb")
    rows = [
        ("State", snapshot["tool_state"]),
        ("Pipeline", snapshot.get("current_pipeline_name")),
        ("Session", snapshot.get("current_session_id")),
        ("Saved", snapshot.get("samples_saved", 0)),
        ("Skipped", snapshot.get("samples_skipped", 0)),
        ("Failed", snapshot.get("samples_failed", 0)),
        ("Disk Free", f"{free:g} GB" if free is not None else None),
        ("Last Error", snapshot.get("last_error")),
    ]
    return "\n".join(f"{label + ':':<13}{value}" for label, value in rows)


def run_control_command(
    command: str,
    pipeline_name: Optional[str] = None,
    *,
    control_topic: str = DEFAULT_CONTROL_TOPIC,
    status_topic: str = DEFAULT_STATUS_TOPIC,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> None:
    """Send one command or print one existing status snapshot; no runtime import."""
    wait_for_master(timeout)
    rospy, String = _import_ros()
    # Explicit argv keeps CLI/config arguments and name remappings out of this
    # independent anonymous node.
    rospy.init_node("data_collect_dag_cli", argv=["data_collect_dag_cli"], anonymous=True, disable_signals=True)
    endpoint = None
    try:
        if command == "status":
            ready = threading.Event()
            result: Dict[str, Any] = {}

            def receive(message: Any) -> None:
                try:
                    payload = json.loads(message.data)
                    if not isinstance(payload, dict) or payload.get("tool_state") not in ("IDLE", "RUNNING", "PAUSED"):
                        raise ValueError("expected a StatusSnapshot JSON object")
                    result["text"] = format_status(payload)
                except (ValueError, TypeError) as exc:
                    result["error"] = str(exc)
                ready.set()

            endpoint = rospy.Subscriber(status_topic, String, receive, queue_size=1)
            if not ready.wait(timeout) or endpoint.get_num_connections() == 0:
                raise RuntimeError(f"no running collection program publishing {status_topic} within {timeout:g} seconds")
            if "error" in result:
                raise RuntimeError(f"invalid status from {status_topic}: {result['error']}")
            print(result["text"])
            return

        # Synchronous publish ensures the one-shot client does not exit with
        # its message still sitting in an asynchronous publisher queue.
        endpoint = rospy.Publisher(control_topic, String, queue_size=None, latch=False)
        deadline = time.monotonic() + timeout
        while endpoint.get_num_connections() == 0:
            if rospy.is_shutdown() or time.monotonic() >= deadline:
                raise RuntimeError(f"no collection control subscriber on {control_topic} within {timeout:g} seconds")
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        payload = {"command": command}
        if command == "start":
            payload["pipeline"] = pipeline_name
        endpoint.publish(String(data=json.dumps(payload, separators=(",", ":"))))
        # rospy can swallow transport write failures and remove the connection.
        if rospy.is_shutdown() or endpoint.get_num_connections() == 0:
            raise RuntimeError(f"control connection lost while sending to {control_topic}")
        print(f"command sent: {command}; execution is asynchronous, check status for the result")
    finally:
        if endpoint is not None:
            endpoint.unregister()
        rospy.signal_shutdown("CLI request complete")
