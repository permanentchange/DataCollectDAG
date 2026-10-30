"""ROS diagnostics and sensor operation commands; imports are lazy."""
import argparse
import bisect
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

DEFAULT_CONFIG = Path(__file__).resolve().parents[2]/'demo/cfg.yaml'


def topics_from_config(path):
    from data_collect_dag.config import load_app_config
    return load_app_config(path).topics


def offsets(reference: list, sensor: list) -> list:
    sensor = sorted(sensor)
    result = []
    for stamp in sorted(reference):
        i = bisect.bisect_left(sensor, stamp)
        candidates = sensor[max(0, i-1):i+1]
        if candidates:
            result.append((min(candidates, key=lambda x: abs(x-stamp))-stamp)*1000)
    return result


def summarize(base: str, reference: list, sensors: dict) -> dict:
    result = {}
    for name, stamps in sensors.items():
        diffs = offsets(reference, stamps)
        absolute = sorted(abs(x) for x in diffs)
        rate = (len(stamps)-1)/(max(stamps)-min(stamps)) if stamps and max(stamps) > min(stamps) else None
        result[name] = {'count': len(stamps), 'rate_hz': rate,
                        'abs_min_ms': min(absolute) if absolute else None,
                        'abs_median_ms': statistics.median(absolute) if absolute else None,
                        'abs_p95_ms': absolute[max(0, math.ceil(len(absolute)*.95)-1)] if absolute else None,
                        'abs_max_ms': max(absolute) if absolute else None,
                        'signed_mean_ms': statistics.mean(diffs) if diffs else None}
    return {'main': base, 'main_count': len(reference), 'topics': result}


def main(base, reference, sensors):
    """Compatibility text formatter, also useful without ROS."""
    report = summarize(base, reference, sensors)
    print(f"Main: {base} ({len(reference)} msgs)")
    for name, row in report['topics'].items():
        if not reference or not sensors[name]:
            print(f"{name}: n/a ({'main' if not reference else 'sensor'} topic has no messages)")
        else:
            print(name+': '+ ' '.join(f'{key}={value:.3f}' for key, value in row.items() if value is not None))
    return report


def read_bag(path: str, topics: list) -> dict:
    import rosbag
    data = {topic: [] for topic in topics}
    with rosbag.Bag(str(path)) as bag:
        for topic, message, _ in bag.read_messages(topics=topics):
            data[topic].append(message.header.stamp.to_sec())
    return data


def sync():
    p = argparse.ArgumentParser(description='Timestamp synchronization report')
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--img-topics', nargs='+'); p.add_argument('--pcd-topics', nargs='+')
    p.add_argument('--main-topic', default='/rslidar_points'); p.add_argument('--bag')
    p.add_argument('--duration', type=float, default=5); p.add_argument('--json', type=Path)
    p.add_argument('--max-offset-ms', type=float)
    a = p.parse_args()
    if not math.isfinite(a.duration) or a.duration <= 0 or (a.max_offset_ms is not None and (not math.isfinite(a.max_offset_ms) or a.max_offset_ms < 0)):
        p.error('positive finite duration and nonnegative finite offset required')
    configured = topics_from_config(a.config)
    images = a.img_topics or [x.topic for x in configured.values() if x.role == 'image']
    clouds = a.pcd_topics or [x.topic for x in configured.values() if x.role == 'pointcloud']
    topics = list(dict.fromkeys(images+clouds+[a.main_topic]))
    if a.bag:
        data = read_bag(a.bag, topics)
    else:
        import rospy
        from sensor_msgs.msg import Image, PointCloud2
        import threading
        rospy.init_node('sync_check', anonymous=True)
        data = {topic: [] for topic in topics}
        lock = threading.Lock()
        def receive(message, topic):
            with lock:
                data[topic].append(message.header.stamp.to_sec())
        subscribers = [rospy.Subscriber(topic, Image if topic in images else PointCloud2, receive, topic) for topic in topics]
        try:
            deadline = time.monotonic()+a.duration
            while time.monotonic() < deadline and not rospy.is_shutdown():
                time.sleep(min(.05, max(0, deadline-time.monotonic())))
        finally:
            for subscriber in subscribers:
                subscriber.unregister()
        with lock:
            data = {key: list(value) for key, value in data.items()}
    reference = data.pop(a.main_topic)
    report = main(a.main_topic, reference, data)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(report, indent=2))
    return int(a.max_offset_ms is not None and any(row['abs_max_ms'] is None or row['abs_max_ms'] > a.max_offset_ms for row in report['topics'].values()))


def run_child(command):
    import signal
    child = subprocess.Popen(command)
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_args: child.terminate())
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.send_signal(__import__('signal').SIGINT)
        return child.wait()
    finally:
        signal.signal(signal.SIGTERM, previous)
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


def record():
    p = argparse.ArgumentParser(description='Record configured ROS topics')
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--topics', nargs='+'); p.add_argument('-o', '--output', default='sensor_data.bag')
    p.add_argument('--split-duration', type=float, default=60); p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if not math.isfinite(a.split_duration) or a.split_duration <= 0:
        p.error('split duration must be finite and positive')
    topics = a.topics or [topic.topic for topic in topics_from_config(a.config).values()]
    command = ['rosbag', 'record', '-O', a.output, '--split', '--duration='+str(a.split_duration)] + topics
    print(command)
    return 0 if a.dry_run else run_child(command)


def camera_reset():
    p = argparse.ArgumentParser(description='Stop camera nodes and start the camera launch file')
    p.add_argument('--nodes', nargs='+', default=['/plan3a_stream_node', '/miivii_gmsl_camera_node'])
    p.add_argument('--launch-package', default='miivii_gmsl_camera'); p.add_argument('--launch-file', default='data_collect.launch')
    p.add_argument('--log-file', type=Path, default=Path('/tmp/data_collect.launch.log'))
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    launch = ['roslaunch', a.launch_package, a.launch_file]
    print({'stop': a.nodes, 'launch': launch, 'log': str(a.log_file)})
    if a.dry_run:
        return 0
    def nodes():
        return subprocess.check_output(['rosnode', 'list'], text=True, timeout=5).splitlines()
    for node in a.nodes:
        if node not in nodes():
            continue
        subprocess.run(['rosnode', 'kill', node], check=True, timeout=5)
        deadline = time.monotonic()+10
        while node in nodes():
            if time.monotonic() >= deadline:
                raise RuntimeError(f'camera node did not stop: {node}')
            time.sleep(.2)
    a.log_file.parent.mkdir(parents=True, exist_ok=True)
    with a.log_file.open('a') as log:
        child = subprocess.Popen(launch, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    print(f'process created: PID={child.pid}; check {a.log_file} for camera readiness')
    return 0
