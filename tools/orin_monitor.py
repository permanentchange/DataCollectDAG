#!/usr/bin/env python3
"""Low-overhead resource monitor for NVIDIA Jetson Orin NX.

Uses NVIDIA tegrastats as the primary Jetson source and /proc/sysfs for
system-level deltas. Requires only Python 3 standard library.
"""

import argparse
import csv
import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime


CPU_COUNT = os.cpu_count() or 8
TEMP_NAMES = ["cpu", "gpu", "cv0", "cv1", "cv2", "soc0", "soc1", "soc2", "tj"]
TEGRA_POWER_RAILS = ["vdd_in", "vdd_cpu_gpu_cv", "vdd_soc"]


def _number(text):
    return None if text is None else float(text)


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def parse_tegrastats_line(line):
    """Parse one tegrastats line into a flat dictionary."""
    out = {}

    m = re.search(r"\bRAM\s+(\d+)/(\d+)MB", line)
    if m:
        out["ram_used_mb"] = int(m.group(1))
        out["ram_total_mb"] = int(m.group(2))

    m = re.search(r"\(lfb\s+(\d+)x(\d+)MB\)", line)
    if m:
        out["lfb_blocks"] = int(m.group(1))
        out["lfb_block_mb"] = int(m.group(2))

    m = re.search(r"\bSWAP\s+(\d+)/(\d+)MB", line)
    if m:
        out["swap_used_mb"] = int(m.group(1))
        out["swap_total_mb"] = int(m.group(2))

    m = re.search(r"\bCPU\s*\[([^]]+)\]", line)
    if m:
        for i, token in enumerate(m.group(1).split(",")):
            token = token.strip()
            if token.lower() == "off":
                out[f"cpu{i}_state"] = "off"
                out[f"cpu{i}_pct"] = None
                out[f"cpu{i}_mhz"] = None
                continue
            cm = re.match(r"([\d.]+)%(?:@([\d.]+))?", token)
            if cm:
                out[f"cpu{i}_state"] = "on"
                out[f"cpu{i}_pct"] = _number(cm.group(1))
                out[f"cpu{i}_mhz"] = _number(cm.group(2))

    def parse_freq(prefix, pct_key, mhz_key):
        fm = re.search(rf"\b{re.escape(prefix)}\s+([\d.]+)%(?:@([\d.]+))?", line)
        if fm:
            out[pct_key] = _number(fm.group(1))
            out[mhz_key] = _number(fm.group(2))

    parse_freq("EMC_FREQ", "emc_pct", "emc_mhz")
    parse_freq("GR3D_FREQ", "gpu_pct", "gpu_mhz")
    parse_freq("GR3D2_FREQ", "gr3d2_pct", "gr3d2_mhz")

    for name, value in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)@(-?[\d.]+)C\b", line):
        key = _norm(name)
        if key in TEMP_NAMES:
            out[f"temp_{key}_c"] = float(value)

    # Typical format: VDD_IN 12600mW/12000mW
    for rail, current, average in re.findall(
        r"\b([A-Z][A-Z0-9_]+)\s+([\d.]+)mW/([\d.]+)mW", line
    ):
        key = _norm(rail)
        out[f"power_{key}_mw"] = float(current)
        out[f"power_{key}_avg_mw"] = float(average)

    return out


def infer_cpu_count(parsed, fallback):
    indices = []
    for key in parsed:
        m = re.fullmatch(r"cpu(\d+)_pct", key)
        if m:
            indices.append(int(m.group(1)))
    return max(indices) + 1 if indices else fallback


def read_proc_stat(path="/proc/stat"):
    result = {}
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            if not line.startswith("cpu"):
                break
            parts = line.split()
            result[parts[0]] = [int(x) for x in parts[1:]]
    return result


def cpu_usage_delta(prev, curr):
    a = prev.get("cpu")
    b = curr.get("cpu")
    if not a or not b:
        return None, None
    n = min(len(a), len(b))
    delta = [max(0, b[i] - a[i]) for i in range(n)]
    total = sum(delta)
    if total <= 0:
        return None, None
    idle = delta[3] if n > 3 else 0
    iowait = delta[4] if n > 4 else 0
    busy_pct = 100.0 * max(0, total - idle - iowait) / total
    iowait_pct = 100.0 * iowait / total
    return busy_pct, iowait_pct


def read_diskstats(path="/proc/diskstats"):
    out = {}
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            p = line.split()
            if len(p) < 14:
                continue
            out[p[2]] = {
                "read_sectors": int(p[5]),
                "write_sectors": int(p[9]),
                "io_ms": int(p[12]),
            }
    return out


def disk_rates(prev, curr, elapsed_s, devices):
    if elapsed_s <= 0:
        return None, None, None
    read_sectors = write_sectors = io_ms = 0
    found = False
    for dev in devices:
        if dev not in prev or dev not in curr:
            continue
        found = True
        read_sectors += max(0, curr[dev]["read_sectors"] - prev[dev]["read_sectors"])
        write_sectors += max(0, curr[dev]["write_sectors"] - prev[dev]["write_sectors"])
        io_ms += max(0, curr[dev]["io_ms"] - prev[dev]["io_ms"])
    if not found:
        return None, None, None
    # Linux /proc/diskstats sector counters are expressed in 512-byte sectors.
    read_mib_s = read_sectors * 512.0 / (1024.0 * 1024.0) / elapsed_s
    write_mib_s = write_sectors * 512.0 / (1024.0 * 1024.0) / elapsed_s
    busy_pct = io_ms / (elapsed_s * 1000.0) * 100.0
    return read_mib_s, write_mib_s, busy_pct


def read_netdev(path="/proc/net/dev"):
    out = {}
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            if ":" not in line:
                continue
            name, values = line.split(":", 1)
            p = values.split()
            if len(p) >= 16:
                out[name.strip()] = (int(p[0]), int(p[8]))
    return out


def net_rates(prev, curr, elapsed_s, interfaces):
    if elapsed_s <= 0:
        return None, None
    rx = tx = 0
    found = False
    for iface in interfaces:
        if iface not in prev or iface not in curr:
            continue
        found = True
        rx += max(0, curr[iface][0] - prev[iface][0])
        tx += max(0, curr[iface][1] - prev[iface][1])
    if not found:
        return None, None
    scale = 1024.0 * 1024.0 * elapsed_s
    return rx / scale, tx / scale


def read_meminfo(path="/proc/meminfo"):
    wanted = {"MemTotal", "MemAvailable", "MemFree", "Cached", "SwapTotal", "SwapFree"}
    out = {}
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            if key in wanted:
                try:
                    out[key] = int(rest.split()[0]) / 1024.0
                except (ValueError, IndexError):
                    pass
    return out


def read_loadavg(path="/proc/loadavg"):
    try:
        p = open(path, "r", encoding="ascii").read().split()
        running, total = p[3].split("/", 1)
        return float(p[0]), float(p[1]), float(p[2]), int(running), int(total)
    except (OSError, ValueError, IndexError):
        return None, None, None, None, None


def discover_block_devices():
    devices = []
    for path in sorted(glob.glob("/sys/block/*")):
        name = os.path.basename(path)
        if name.startswith(("loop", "ram", "zram", "fd", "sr")):
            continue
        devices.append(name)
    return devices


def discover_network_interfaces():
    skip_exact = {"lo", "docker0", "l4tbr0"}
    skip_prefix = ("veth", "br-", "can")
    result = []
    for path in sorted(glob.glob("/sys/class/net/*")):
        name = os.path.basename(path)
        if name in skip_exact or name.startswith(skip_prefix):
            continue
        result.append(name)
    return result


def discover_devfreq_sources():
    sources = {}
    for path in glob.glob("/sys/class/devfreq/*"):
        name = os.path.basename(path).lower()
        try:
            n = open(os.path.join(path, "name"), "r", encoding="ascii").read().strip().lower()
            name += " " + n
        except OSError:
            pass
        cur = os.path.join(path, "cur_freq")
        if not os.path.isfile(cur):
            continue
        if "emc" in name and "emc" not in sources:
            sources["emc"] = cur
        if any(marker in name for marker in ("ga10b", "gpu", "17000000")) and "gpu" not in sources:
            sources["gpu"] = cur
    return sources


def read_devfreq_mhz(path):
    if not path:
        return None
    try:
        v = float(open(path, "r", encoding="ascii").read().strip())
    except (OSError, ValueError):
        return None
    # devfreq cur_freq is normally Hz; tolerate kHz sources as well.
    if v >= 10_000_000:
        return v / 1_000_000.0
    if v >= 10_000:
        return v / 1000.0
    return v


def discover_hwmon_power_sensors():
    """Discover direct power inputs or INA-style voltage/current pairs once."""
    result = {}
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            hwname = open(os.path.join(hw, "name"), "r", encoding="ascii").read().strip()
        except OSError:
            hwname = os.path.basename(hw)

        # Direct power*_input values use microwatts per hwmon ABI.
        for p in glob.glob(os.path.join(hw, "power*_input")):
            stem = os.path.basename(p).split("_")[0]
            label_path = os.path.join(hw, stem + "_label")
            try:
                label = open(label_path, "r", encoding="ascii").read().strip()
            except OSError:
                label = f"{hwname}_{stem}"
            key = _norm(label)
            if key:
                result.setdefault(key, ("uw", p, None))

        # INA3221 commonly exposes inN_input (mV) + currN_input (mA).
        for vp in glob.glob(os.path.join(hw, "in*_input")):
            m = re.match(r"in(\d+)_input$", os.path.basename(vp))
            if not m:
                continue
            idx = m.group(1)
            cp = os.path.join(hw, f"curr{idx}_input")
            if not os.path.isfile(cp):
                continue
            labels = [os.path.join(hw, f"in{idx}_label"), os.path.join(hw, f"curr{idx}_label")]
            label = None
            for lp in labels:
                try:
                    label = open(lp, "r", encoding="ascii").read().strip()
                    if label:
                        break
                except OSError:
                    pass
            key = _norm(label or f"{hwname}_ch{idx}")
            if key:
                result.setdefault(key, ("mv_ma", vp, cp))
    return result


def read_hwmon_power_mw(sensor):
    kind, a, b = sensor
    try:
        av = float(open(a, "r", encoding="ascii").read().strip())
        if kind == "uw":
            return av / 1000.0
        bv = float(open(b, "r", encoding="ascii").read().strip())
        return av * bv / 1000.0
    except (OSError, ValueError, TypeError):
        return None


def parse_duration(text):
    if text is None:
        return 0.0
    s = str(text).strip().lower()
    if s in ("", "0", "forever", "inf"):
        return 0.0
    m = re.fullmatch(r"([\d.]+)\s*([smh]?)", s)
    if not m:
        raise argparse.ArgumentTypeError("duration must look like 30, 30s, 20m, or 1.5h")
    value = float(m.group(1))
    return value * {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[m.group(2)]


def fmt(v, digits=1):
    if v is None or v == "":
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def build_fieldnames(cpu_count, hwmon_power_keys, include_raw=False):
    fields = [
        "timestamp_iso", "elapsed_s", "sample",
        "cpu_total_pct", "cpu_iowait_pct", "load1", "load5", "load15",
        "processes_running", "processes_total",
        "ram_used_mb", "ram_total_mb", "ram_used_pct", "mem_available_mb",
        "swap_used_mb", "swap_total_mb", "lfb_blocks", "lfb_block_mb",
    ]
    for i in range(cpu_count):
        fields += [f"cpu{i}_pct", f"cpu{i}_mhz", f"cpu{i}_state"]
    fields += ["emc_pct", "emc_mhz", "gpu_pct", "gpu_mhz", "gr3d2_pct", "gr3d2_mhz"]
    fields += [f"temp_{x}_c" for x in TEMP_NAMES]
    for rail in TEGRA_POWER_RAILS:
        fields += [f"power_{rail}_mw", f"power_{rail}_avg_mw"]
    fields += [f"hwmon_power_{k}_mw" for k in hwmon_power_keys]
    fields += [
        "disk_read_mib_s", "disk_write_mib_s", "disk_busy_pct",
        "net_rx_mib_s", "net_tx_mib_s",
    ]
    if include_raw:
        fields.append("raw_tegrastats")
    return fields


def main(argv=None):
    p = argparse.ArgumentParser(description="Low-overhead NVIDIA Jetson Orin NX resource monitor")
    p.add_argument("-i", "--interval", type=float, default=1.0, help="sampling interval in seconds (default: 1.0)")
    p.add_argument("-d", "--duration", type=parse_duration, default=0.0, help="duration: 30s, 20m, 1h; 0=until Ctrl+C")
    p.add_argument("-o", "--output", default=None, help="CSV output path")
    p.add_argument("--disk", default="auto", help="comma-separated block devices, or auto")
    p.add_argument("--net", default="auto", help="comma-separated network interfaces, or auto")
    p.add_argument("--print-every", type=int, default=5, help="print one console summary every N samples; 0 disables")
    p.add_argument("--raw", action="store_true", help="also store the original tegrastats line")
    args = p.parse_args(argv)

    if args.interval < 0.2:
        p.error("interval below 0.2s is intentionally rejected to keep monitor overhead low")

    tegrastats = shutil.which("tegrastats")
    if not tegrastats:
        p.error("tegrastats not found in PATH")

    output = args.output or f"orin_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    devices = discover_block_devices() if args.disk == "auto" else [x for x in args.disk.split(",") if x]
    interfaces = discover_network_interfaces() if args.net == "auto" else [x for x in args.net.split(",") if x]
    devfreq = discover_devfreq_sources()
    hwmon = discover_hwmon_power_sensors()

    interval_ms = max(200, int(round(args.interval * 1000.0)))
    cmd = [tegrastats, "--interval", str(interval_ms)]

    print(f"output: {output}")
    print(f"interval: {interval_ms / 1000.0:.3f}s   duration: {'until Ctrl+C' if args.duration <= 0 else str(args.duration) + 's'}")
    print(f"disk devices: {','.join(devices) if devices else '-'}")
    print(f"network interfaces: {','.join(interfaces) if interfaces else '-'}")
    if hwmon:
        print("hwmon power: " + ",".join(sorted(hwmon)))
    else:
        print("hwmon power: not discovered (tegrastats power fields will still be used if present)")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    old_handlers = {}
    stop = {"value": False}

    def request_stop(signum, frame):
        stop["value"] = True

    for sig in (signal.SIGTERM, signal.SIGHUP):
        old_handlers[sig] = signal.signal(sig, request_stop)

    start = time.monotonic()
    prev_t = start
    prev_cpu = read_proc_stat()
    prev_disk = read_diskstats()
    prev_net = read_netdev()
    samples = 0

    try:
        pending_line = proc.stdout.readline() if proc.stdout else ""
        if not pending_line:
            raise RuntimeError(f"tegrastats produced no data (exit code {proc.poll()})")

        first_parsed = parse_tegrastats_line(pending_line)
        cpu_count = infer_cpu_count(first_parsed, CPU_COUNT)
        fieldnames = build_fieldnames(cpu_count, sorted(hwmon), args.raw)
        print(f"CPU columns: {cpu_count} cores (detected from tegrastats)")

        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()

            while not stop["value"]:
                if pending_line is not None:
                    line = pending_line
                    pending_line = None
                else:
                    line = proc.stdout.readline() if proc.stdout else ""

                if not line:
                    if proc.poll() is not None:
                        raise RuntimeError(f"tegrastats exited unexpectedly with code {proc.returncode}")
                    time.sleep(0.02)
                    continue

                now_mono = time.monotonic()
                elapsed_delta = now_mono - prev_t
                row = parse_tegrastats_line(line)
                row["timestamp_iso"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
                row["elapsed_s"] = round(now_mono - start, 3)
                row["sample"] = samples + 1

                curr_cpu = read_proc_stat()
                curr_disk = read_diskstats()
                curr_net = read_netdev()
                cpu_pct, iowait_pct = cpu_usage_delta(prev_cpu, curr_cpu)
                dr, dw, db = disk_rates(prev_disk, curr_disk, elapsed_delta, devices)
                nr, nt = net_rates(prev_net, curr_net, elapsed_delta, interfaces)
                row.update({
                    "cpu_total_pct": cpu_pct,
                    "cpu_iowait_pct": iowait_pct,
                    "disk_read_mib_s": dr,
                    "disk_write_mib_s": dw,
                    "disk_busy_pct": db,
                    "net_rx_mib_s": nr,
                    "net_tx_mib_s": nt,
                })

                load1, load5, load15, prun, ptotal = read_loadavg()
                row.update({
                    "load1": load1, "load5": load5, "load15": load15,
                    "processes_running": prun, "processes_total": ptotal,
                })

                mem = read_meminfo()
                if "MemAvailable" in mem:
                    row["mem_available_mb"] = mem["MemAvailable"]
                if "ram_total_mb" not in row and "MemTotal" in mem:
                    row["ram_total_mb"] = mem["MemTotal"]
                if "ram_used_mb" not in row and "MemTotal" in mem and "MemAvailable" in mem:
                    row["ram_used_mb"] = mem["MemTotal"] - mem["MemAvailable"]
                if row.get("ram_total_mb"):
                    row["ram_used_pct"] = 100.0 * row.get("ram_used_mb", 0.0) / row["ram_total_mb"]
                if "swap_total_mb" not in row and "SwapTotal" in mem:
                    row["swap_total_mb"] = mem["SwapTotal"]
                if "swap_used_mb" not in row and "SwapTotal" in mem and "SwapFree" in mem:
                    row["swap_used_mb"] = mem["SwapTotal"] - mem["SwapFree"]

                if row.get("gpu_mhz") is None and "gpu" in devfreq:
                    row["gpu_mhz"] = read_devfreq_mhz(devfreq["gpu"])
                if row.get("emc_mhz") is None and "emc" in devfreq:
                    row["emc_mhz"] = read_devfreq_mhz(devfreq["emc"])

                for key, sensor in hwmon.items():
                    row[f"hwmon_power_{key}_mw"] = read_hwmon_power_mw(sensor)

                if args.raw:
                    row["raw_tegrastats"] = line.rstrip("\n")

                writer.writerow(row)
                samples += 1
                if samples % 10 == 0:
                    f.flush()

                if args.print_every > 0 and (samples == 1 or samples % args.print_every == 0):
                    print(
                        f"[{samples:6d}] CPU {fmt(cpu_pct)}%  "
                        f"GPU {fmt(row.get('gpu_pct'))}%@{fmt(row.get('gpu_mhz'), 0)}MHz  "
                        f"EMC {fmt(row.get('emc_pct'))}%@{fmt(row.get('emc_mhz'), 0)}MHz  "
                        f"RAM {fmt(row.get('ram_used_mb'), 0)}/{fmt(row.get('ram_total_mb'), 0)}MB  "
                        f"Tcpu {fmt(row.get('temp_cpu_c'))}C  "
                        f"disk R/W {fmt(dr, 2)}/{fmt(dw, 2)} MiB/s  "
                        f"net R/T {fmt(nr, 2)}/{fmt(nt, 2)} MiB/s"
                    )

                prev_t = now_mono
                prev_cpu, prev_disk, prev_net = curr_cpu, curr_disk, curr_net

                if args.duration > 0 and now_mono - start >= args.duration:
                    break

    except KeyboardInterrupt:
        pass
    finally:
        stop["value"] = True
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        for sig, old in old_handlers.items():
            signal.signal(sig, old)

    print(f"stopped: {samples} samples written to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
