#!/usr/bin/env python3
import argparse,csv,glob,os,re,shutil,subprocess,sys,time
from datetime import datetime

VERSION="2.1.0"
TEMPS=["cpu","gpu","cv0","cv1","cv2","soc0","soc1","soc2","tj"]
VIRTUAL_NET=("lo","docker","veth","br-","l4tbr","virbr")

def read_text(p):
    try:
        with open(p,"r",encoding="utf-8",errors="replace") as f:return f.read().strip()
    except OSError:return None

def read_float(p):
    s=read_text(p)
    try:return float(s.split()[0]) if s is not None else None
    except (ValueError,IndexError):return None

def norm(s):return re.sub(r"[^a-z0-9]+","_",str(s).strip().lower()).strip("_")

def parse_duration(s):
    if s is None:return None
    m=re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([smhd]?)",s.strip().lower())
    if not m:raise argparse.ArgumentTypeError("duration: 30s / 10m / 1.5h / 1d")
    return float(m.group(1))*{"":1,"s":1,"m":60,"h":3600,"d":86400}[m.group(2)]

def freq_mhz(v):
    if v is None:return None
    v=float(v)
    if abs(v)>=10_000_000:return v/1_000_000
    if abs(v)>=100_000:return v/1_000
    return v

def parse_ts(line):
    o={"cpu":[],"ram_used_mb":None,"ram_total_mb":None,"swap_used_mb":None,"swap_total_mb":None,
       "swap_cached_mb":None,"lfb_count":None,"lfb_size_mb":None,"gpu_pct":None,"gpu_mhz":None,
       "gr3d2_pct":None,"gr3d2_mhz":None,"emc_pct":None,"emc_mhz":None}

    m=re.search(r"\bRAM\s+(\d+)/(\d+)MB",line)
    if m:o["ram_used_mb"],o["ram_total_mb"]=map(int,m.groups())

    m=re.search(r"\(lfb\s+(\d+)x(\d+)MB\)",line)
    if m:o["lfb_count"],o["lfb_size_mb"]=map(int,m.groups())

    m=re.search(r"\bSWAP\s+(\d+)/(\d+)MB(?:\s+\(cached\s+(\d+)MB\))?",line)
    if m:
        o["swap_used_mb"],o["swap_total_mb"]=int(m.group(1)),int(m.group(2))
        if m.group(3):o["swap_cached_mb"]=int(m.group(3))

    m=re.search(r"\bCPU\s*\[([^\]]*)\](?:@(\d+(?:\.\d+)?))?",line)
    if m:
        shared=float(m.group(2)) if m.group(2) else None
        for x in m.group(1).split(","):
            x=x.strip()
            if x.lower()=="off":o["cpu"].append({"pct":None,"mhz":None});continue
            cm=re.match(r"([0-9.]+)%?(?:@(\d+(?:\.\d+)?))?",x)
            if cm:o["cpu"].append({"pct":float(cm.group(1)),"mhz":float(cm.group(2)) if cm.group(2) else shared})

    for key,pat in (
        ("emc",r"\bEMC_FREQ\s+([0-9.]+)%(?:@(\d+(?:\.\d+)?))?"),
        ("gpu",r"\bGR3D_FREQ\s+([0-9.]+)%(?:@(\d+(?:\.\d+)?))?"),
        ("gr3d2",r"\bGR3D2_FREQ\s+([0-9.]+)%(?:@(\d+(?:\.\d+)?))?")
    ):
        m=re.search(pat,line)
        if m:
            o[f"{key}_pct"]=float(m.group(1))
            if m.group(2):o[f"{key}_mhz"]=float(m.group(2))

    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9_-]*)@(-?[0-9.]+)C\b",line):
        o[f"temp_{norm(m.group(1))}_c"]=float(m.group(2))
    return o

def find_ts():
    p=shutil.which("tegrastats")
    if p:return p
    for p in ("/usr/bin/tegrastats","/home/nvidia/tegrastats"):
        if os.path.isfile(p) and os.access(p,os.X_OK):return p
    return None

def start_ts(path,interval):
    return subprocess.Popen([path,"--interval",str(max(20,round(interval*1000)))],
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)

def next_ts(proc):
    if not proc.stdout:return None
    while True:
        line=proc.stdout.readline()
        if line=="":return None
        line=line.strip()
        if "RAM " in line and "CPU" in line:return line

def proc_stat():
    out={}
    for line in (read_text("/proc/stat") or "").splitlines():
        p=line.split()
        if not p or not p[0].startswith("cpu"):continue
        if p[0]!="cpu" and not p[0][3:].isdigit():continue
        try:out[p[0]]=[int(x) for x in p[1:]]
        except ValueError:pass
    return out

def cpu_delta(a,b):
    if not a or not b:return None,None
    d=[max(0,b[i]-a[i]) for i in range(min(len(a),len(b)))]
    total=sum(d)
    if total<=0:return None,None
    idle=d[3] if len(d)>3 else 0;iowait=d[4] if len(d)>4 else 0
    return 100*(total-idle-iowait)/total,100*iowait/total

def loadavg():
    try:
        p=read_text("/proc/loadavg").split()
        return float(p[0]),float(p[1]),float(p[2])
    except:return None,None,None

def meminfo():
    text=read_text("/proc/meminfo") or "";d={}
    for line in text.splitlines():
        m=re.match(r"([^:]+):\s+(\d+)",line)
        if m:d[m.group(1)]=int(m.group(2))
    return text,d

def nvmap_mb(text):
    m=re.search(r"^NvMapMemUsed:\s+(\d+)\s+kB",text,re.M)
    return int(m.group(1))/1024 if m else None

def default_disks():
    return [os.path.basename(p) for p in sorted(glob.glob("/sys/block/*"))
            if not os.path.basename(p).startswith(("loop","zram","ram","sr","fd"))]

def diskstats(devs):
    want=set(devs);rd=wr=io=found=0
    for line in (read_text("/proc/diskstats") or "").splitlines():
        p=line.split()
        if len(p)<14 or p[2] not in want:continue
        try:rd+=int(p[5]);wr+=int(p[9]);io+=int(p[12]);found+=1
        except ValueError:pass
    return (rd,wr,io) if found else None

def disk_delta(a,b,dt):
    if a is None or b is None or dt<=0:return None,None,None
    rd=max(0,b[0]-a[0])*512/1048576/dt
    wr=max(0,b[1]-a[1])*512/1048576/dt
    busy=min(100,max(0,b[2]-a[2])/(dt*1000)*100)
    return rd,wr,busy

def default_net():
    out=[]
    for line in (read_text("/proc/net/dev") or "").splitlines()[2:]:
        if ":" not in line:continue
        n=line.split(":",1)[0].strip()
        if not n.startswith(VIRTUAL_NET):out.append(n)
    return sorted(set(out))

def netstats(devs):
    want=set(devs);rx=tx=found=0
    for line in (read_text("/proc/net/dev") or "").splitlines()[2:]:
        if ":" not in line:continue
        n,s=line.split(":",1)
        if n.strip() not in want:continue
        p=s.split()
        if len(p)<16:continue
        try:rx+=int(p[0]);tx+=int(p[8]);found+=1
        except ValueError:pass
    return (rx,tx) if found else None

def net_delta(a,b,dt):
    if a is None or b is None or dt<=0:return None,None
    return max(0,b[0]-a[0])/1048576/dt,max(0,b[1]-a[1])/1048576/dt

def is_emc(path):
    return "emc" in (path+" "+os.path.realpath(path)).lower() or "memory-controller" in (path+" "+os.path.realpath(path)).lower()

def discover_emc_freq():
    for p in ("/sys/kernel/debug/bpmp/debug/clk/emc/rate",
              "/sys/kernel/debug/bpmp/debug/clk/emc/clk_rate",
              "/sys/kernel/debug/clk/emc/clk_rate"):
        if os.path.isfile(p) and os.access(p,os.R_OK):return p
    for p in sorted(glob.glob("/sys/class/devfreq/*/cur_freq")):
        if is_emc(os.path.dirname(p)) and os.access(p,os.R_OK):return p
    return None

def discover_emc_load():
    for base in glob.glob("/sys/class/devfreq/*"):
        if not is_emc(base):continue
        for n in ("load","utilization","busy_percent","device_load"):
            p=os.path.join(base,n)
            if os.path.isfile(p) and os.access(p,os.R_OK):return p
    return None

def discover_gpu_freq():
    for p in ("/sys/kernel/debug/bpmp/debug/clk/gpu/rate","/sys/kernel/debug/clk/gpu/clk_rate"):
        if os.path.isfile(p) and os.access(p,os.R_OK):return p
    for p in sorted(glob.glob("/sys/class/devfreq/*/cur_freq")):
        base=os.path.dirname(p);s=(base+" "+os.path.realpath(base)).lower()
        if ("gpu" in s or "17000000" in s) and os.access(p,os.R_OK):return p
    return None

def read_freq(path):return freq_mhz(read_float(path)) if path else None

def read_pct(path):
    s=read_text(path) if path else None
    if s is None:return None
    m=re.search(r"([0-9]+(?:\.[0-9]+)?)",s)
    if not m:return None
    v=float(m.group(1))
    return v if 0<=v<=100 else None

def rail_name(path,ch):
    if "0040" not in path.lower():return None
    return {1:"vdd_in",2:"vdd_cpu_gpu_cv",3:"vdd_soc"}.get(ch)

def discover_power():
    out={}
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        real=os.path.realpath(hw);name=norm(read_text(os.path.join(hw,"name")) or "")
        if "ina3221" not in name and "ina3221" not in real.lower():continue
        for ch in range(1,5):
            label=read_text(os.path.join(hw,f"power{ch}_label")) or read_text(os.path.join(hw,f"curr{ch}_label")) or read_text(os.path.join(hw,f"in{ch}_label"))
            rail=norm(label) if label else rail_name(real,ch)
            if not rail:continue
            p=os.path.join(hw,f"power{ch}_input");v=os.path.join(hw,f"in{ch}_input");c=os.path.join(hw,f"curr{ch}_input")
            if os.path.isfile(p) and os.access(p,os.R_OK):out[rail]=("power",p)
            elif all(os.path.isfile(x) and os.access(x,os.R_OK) for x in (v,c)):out[rail]=("vi",v,c)
    return out

def read_power(sensors):
    out={}
    for name,d in sensors.items():
        if d[0]=="power":
            v=read_float(d[1]);out[name]=v/1000 if v is not None else None
        else:
            v=read_float(d[1]);a=read_float(d[2]);out[name]=v*a/1000 if v is not None and a is not None else None
    return out

def header(ncpu,power):
    h=["timestamp","sample","elapsed_s","interval_s","cpu_total_pct","cpu_iowait_pct","load1","load5","load15"]
    h += [f"cpu{i}_pct" for i in range(ncpu)]
    h += [f"cpu{i}_mhz" for i in range(ncpu)]
    h += ["gpu_pct","gpu_mhz","gr3d2_pct","gr3d2_mhz","emc_pct","emc_mhz","emc_sysfs_load_pct",
          "ram_used_mb","ram_total_mb","ram_available_mb","ram_used_pct","swap_used_mb","swap_total_mb",
          "swap_cached_mb","lfb_count","lfb_size_mb","lfb_total_mb","nvmap_used_mb",
          "disk_read_mib_s","disk_write_mib_s","disk_busy_pct","net_rx_mib_s","net_tx_mib_s"]
    h += [f"temp_{x}_c" for x in TEMPS]
    h += [f"power_{x}_mw" for x in power]
    return h

def cell(v):
    if v is None:return ""
    return f"{v:.3f}" if isinstance(v,float) else v

def split_arg(s):return [x.strip() for x in s.split(",") if x.strip()] if s else None

def args():
    p=argparse.ArgumentParser()
    p.add_argument("-i","--interval",type=float,default=1.0)
    p.add_argument("-d","--duration",type=parse_duration)
    p.add_argument("-o","--output",default="orin_monitor.csv")
    p.add_argument("--disk")
    p.add_argument("--net")
    p.add_argument("--quiet",action="store_true")
    return p.parse_args()

def main():
    a=args()
    if a.interval<0.02:
        print("interval must be >= 0.02s",file=sys.stderr);return 2

    tsp=find_ts()
    if not tsp:
        print("tegrastats not found",file=sys.stderr);return 2

    disks=split_arg(a.disk) or default_disks()
    nets=split_arg(a.net) or default_net()
    sensors=discover_power();power_names=sorted(sensors)
    emc_freq_path=discover_emc_freq()
    emc_load_path=discover_emc_load()
    gpu_freq_path=discover_gpu_freq()

    os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True)
    proc=start_ts(tsp,a.interval)
    sample=0

    try:
        first_line=next_ts(proc)
        if not first_line:
            print("no tegrastats output",file=sys.stderr);return 2

        first=parse_ts(first_line)
        ncpu=len(first["cpu"]) or os.cpu_count() or 8
        cols=header(ncpu,power_names)

        print(f"Orin Monitor v{VERSION}")
        print(f"output: {a.output}")
        print(f"interval: {a.interval:.3f}s  duration: {'Ctrl+C' if a.duration is None else f'{a.duration:.1f}s'}")
        print(f"disk: {','.join(disks) or '-'}")
        print(f"net: {','.join(nets) or '-'}")
        print(f"power: {','.join(power_names) or '-'}")
        print(f"EMC freq: {emc_freq_path or '-'}")
        print(f"EMC load: {emc_load_path or '-'}")
        print(f"GPU freq fallback: {gpu_freq_path or '-'}")
        print(f"CPU cores: {ncpu}")

        prev_cpu=proc_stat();prev_disk=diskstats(disks);prev_net=netstats(nets)
        start=time.monotonic();last=start-a.interval;pending=first_line
        every=max(1,round(1/a.interval))

        with open(a.output,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=cols);w.writeheader()

            while a.duration is None or time.monotonic()-start<a.duration:
                line=pending if pending is not None else next_ts(proc);pending=None
                if not line:break

                now=time.monotonic();dt=max(now-last,1e-9);elapsed=now-start;last=now;sample+=1
                t=parse_ts(line)

                cur_cpu=proc_stat();cpu_pct,iowait=cpu_delta(prev_cpu.get("cpu"),cur_cpu.get("cpu"));prev_cpu=cur_cpu
                cur_disk=diskstats(disks);rd,wr,busy=disk_delta(prev_disk,cur_disk,dt);prev_disk=cur_disk
                cur_net=netstats(nets);rx,tx=net_delta(prev_net,cur_net,dt);prev_net=cur_net
                l1,l5,l15=loadavg()
                mem_text,mem=meminfo()
                pwr=read_power(sensors)

                gpu_mhz=t["gpu_mhz"] if t["gpu_mhz"] is not None else read_freq(gpu_freq_path)
                emc_mhz=t["emc_mhz"] if t["emc_mhz"] is not None else read_freq(emc_freq_path)

                row={
                    "timestamp":datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "sample":sample,"elapsed_s":elapsed,"interval_s":dt,
                    "cpu_total_pct":cpu_pct,"cpu_iowait_pct":iowait,"load1":l1,"load5":l5,"load15":l15,
                    "gpu_pct":t["gpu_pct"],"gpu_mhz":gpu_mhz,
                    "gr3d2_pct":t["gr3d2_pct"],"gr3d2_mhz":t["gr3d2_mhz"],
                    "emc_pct":t["emc_pct"],"emc_mhz":emc_mhz,"emc_sysfs_load_pct":read_pct(emc_load_path),
                    "ram_used_mb":t["ram_used_mb"],"ram_total_mb":t["ram_total_mb"],
                    "ram_available_mb":mem.get("MemAvailable",0)/1024 if "MemAvailable" in mem else None,
                    "ram_used_pct":100*t["ram_used_mb"]/t["ram_total_mb"] if t["ram_used_mb"] is not None and t["ram_total_mb"] else None,
                    "swap_used_mb":t["swap_used_mb"],"swap_total_mb":t["swap_total_mb"],"swap_cached_mb":t["swap_cached_mb"],
                    "lfb_count":t["lfb_count"],"lfb_size_mb":t["lfb_size_mb"],
                    "lfb_total_mb":t["lfb_count"]*t["lfb_size_mb"] if t["lfb_count"] is not None and t["lfb_size_mb"] is not None else None,
                    "nvmap_used_mb":nvmap_mb(mem_text),
                    "disk_read_mib_s":rd,"disk_write_mib_s":wr,"disk_busy_pct":busy,
                    "net_rx_mib_s":rx,"net_tx_mib_s":tx,
                }

                for i in range(ncpu):
                    c=t["cpu"][i] if i<len(t["cpu"]) else None
                    row[f"cpu{i}_pct"]=c["pct"] if c else None
                    row[f"cpu{i}_mhz"]=c["mhz"] if c else None

                for x in TEMPS:row[f"temp_{x}_c"]=t.get(f"temp_{x}_c")
                for x in power_names:row[f"power_{x}_mw"]=pwr.get(x)

                w.writerow({k:cell(row.get(k)) for k in cols})

                if sample%every==0:f.flush()

                if not a.quiet and (sample==1 or sample%every==0):
                    s=lambda v,fmt:"-" if v is None else format(v,fmt)
                    print(
                        f"[{sample:6d}] "
                        f"CPU {s(cpu_pct,'.1f')}%  "
                        f"GPU {s(t['gpu_pct'],'.1f')}%@{s(gpu_mhz,'.0f')}MHz  "
                        f"EMC {s(t['emc_pct'],'.1f')}%@{s(emc_mhz,'.0f')}MHz  "
                        f"RAM {t['ram_used_mb']}/{t['ram_total_mb']}MB  "
                        f"Tcpu {s(t.get('temp_cpu_c'),'.1f')}C  "
                        f"disk {s(rd,'.2f')}/{s(wr,'.2f')} MiB/s  "
                        f"net {s(rx,'.2f')}/{s(tx,'.2f')} MiB/s"
                    )

            f.flush()

    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            try:proc.terminate();proc.wait(timeout=1)
            except:
                try:proc.kill()
                except:pass

    print(f"stopped: {sample} samples written to {a.output}")
    return 0

if __name__=="__main__":
    sys.exit(main())