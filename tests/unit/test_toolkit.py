"""Small offline workflow fixtures for repository tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import numpy as np
from PIL import Image
import pytest

from tests.helpers import minimal_config
from tools.lib import dataset
from tools.lib.bev import height_grid
from tools.lib.cloud_io import load_point_cloud
from tools.lib.geometry import project_camera_points
from tools.lib.projection import render
from tools.lib.ros import offsets, summarize

ROOT = Path(__file__).resolve().parents[2]


def invoke(*args, cwd=None):
    return subprocess.run([sys.executable, str(ROOT/'tools/toolkit.py'), *map(str, args)],
                          cwd=cwd, capture_output=True, text=True, timeout=20)


def make_dataset(root, frames=('1', '2', '3')):
    for name in ('camera_config', 'camera_image_0', 'lidar_point_cloud_0'):
        (root/name).mkdir(parents=True)
        for frame in frames:
            (root/name/(frame+('.json' if name=='camera_config' else '.dat'))).write_text(frame)
    return root


def test_dataset_round_trip_and_archive(tmp_path):
    source = make_dataset(tmp_path/'source')
    parts = tmp_path/'parts'
    assert invoke('dataset', 'split', source, parts, '--dry-run').returncode == 0
    assert not parts.exists()
    result = invoke('dataset', 'split', source, parts, '--part-count', '2')
    assert result.returncode == 0, result.stderr
    original = source/'camera_config/1.json'
    assert os.path.samefile(original, parts/'part01/camera_config/1.json')
    merged = tmp_path/'merged'
    result = invoke('dataset', 'merge', parts/'part01', parts/'part02', '-o', merged)
    assert result.returncode == 0, result.stderr
    assert set(dataset.index(merged)['camera_config']) == {'1','2','3'}
    counts = tmp_path/'counts.json'; counts.write_text('{"1":2,"3":0}')
    selected = tmp_path/'selected'
    assert invoke('dataset', 'select', merged, selected, '--frames', counts).returncode == 0
    assert set(dataset.index(selected)['camera_config']) == {'1'}
    archives = tmp_path/'zips'
    assert invoke('dataset', 'archive', selected, '-o', archives, '--dry-run').returncode == 0
    assert not archives.exists()
    assert invoke('dataset', 'archive', selected, '-o', archives).returncode == 0
    with zipfile.ZipFile(archives/'selected.zip') as bundle:
        assert set(bundle.namelist()) == {'camera_config/1.json','camera_image_0/1.dat','lidar_point_cloud_0/1.dat'}
    assert invoke('dataset', 'archive', selected, '-o', archives).returncode != 0


@pytest.mark.parametrize('problem', ['missing', 'orphan', 'duplicate', 'symlink', 'nested'])
def test_dataset_rejects_invalid_input(tmp_path, problem):
    source = make_dataset(tmp_path/'source')
    folder = source/'camera_image_0'
    if problem == 'missing': (folder/'1.dat').unlink()
    elif problem == 'orphan': (folder/'4.dat').write_text('extra')
    elif problem == 'duplicate': (folder/'1.jpg').write_text('duplicate')
    elif problem == 'symlink': (folder/'link.dat').symlink_to(folder/'1.dat')
    else: (folder/'nested').mkdir()
    result = invoke('dataset','split',source,tmp_path/'output','--dry-run')
    assert result.returncode != 0
    assert not (tmp_path/'output').exists()


def test_merge_collision_and_link_failure(tmp_path, monkeypatch):
    source = make_dataset(tmp_path/'source')
    assert invoke('dataset','merge',source,source,'-o',tmp_path/'out').returncode != 0
    def fail(*args): raise OSError('cross filesystem')
    monkeypatch.setattr(dataset.os, 'link', fail)
    with pytest.raises(OSError, match='incomplete outputs'):
        dataset.execute([(tmp_path/'output',dataset.index(source),['1'])])
    assert (source/'camera_config/1.json').read_text() == '1'


def test_negative_height_bev_and_empty_cells():
    image = height_grid(np.array([[.2,.2,-1.],[.2,.2,-.5]]), (0,2,0,2), 1, -2, 2)
    assert image.shape == (2,2)
    assert image[1,0] == 96
    assert np.count_nonzero(image) == 1
    with pytest.raises(ValueError): height_grid(np.empty((0,3)),(0,2,0,2),0,-2,2)


@pytest.mark.parametrize('binary', [False, True])
def test_pcd_field_order_and_truncation(tmp_path, binary):
    pcd = tmp_path/'points.pcd'
    header='FIELDS intensity z x y\nSIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\nPOINTS 1\nDATA '+('binary' if binary else 'ascii')+'\n'
    pcd.write_bytes(header.encode()+(np.array([9,3,1,2],dtype='<f4').tobytes() if binary else b'9 3 1 2\n'))
    np.testing.assert_allclose(load_point_cloud(pcd).points, [[1,2,3]])
    pcd.write_bytes(header.replace('POINTS 1','POINTS 2').encode()+pcd.read_bytes()[len(header):])
    with pytest.raises(ValueError): load_point_cloud(pcd)


def test_projection_indices_and_fisheye():
    k=np.array([[10,0,10],[0,10,10],[0,0,1.]])
    points=np.array([[1,0,2],[0,0,-1],[100,0,1],[np.nan,0,1]])
    pixels,indices,stats=project_camera_points(points,k,np.eye(4),20,20)
    np.testing.assert_array_equal(indices,[0]); np.testing.assert_array_equal(pixels,[[15,10]])
    assert stats['points_rejected']==3
    pixels,indices,_=project_camera_points(points[:1],k,np.eye(4),20,20,'equidistant',[0]*4)
    np.testing.assert_array_equal(indices,[0]); assert pixels.shape==(1,2)
    with pytest.raises(ValueError): project_camera_points(points,k,np.eye(4),20,20,'unknown')


def test_projection_writes_report(tmp_path):
    from data_collect_dag.io_utils import save_ascii_pcd
    for folder in ('camera_config','camera_image_0','lidar_point_cloud_0'): (tmp_path/folder).mkdir()
    Image.new('RGB',(20,20)).save(tmp_path/'camera_image_0/1.jpg')
    cam={'camera_internal':{'fx':10,'fy':10,'cx':10,'cy':10},'camera_external':np.eye(4).ravel().tolist(),'rowMajor':True,'dist_model':'equidistant','dist_coeffs':[0]*4}
    (tmp_path/'camera_config/1.json').write_text(json.dumps({'items':[cam]}))
    save_ascii_pcd(tmp_path/'lidar_point_cloud_0/1.pcd',np.array([[0,0,1.]]))
    report=json.loads(render(tmp_path,'1',suffix='_test').read_text())
    assert report['cameras'][0]['points_in_image']==1
    image=Path(report['cameras'][0]['projection_image']); assert image.exists()
    assert Image.open(image).size==(20,20)


def test_offline_viewer(tmp_path):
    from data_collect_dag.io_utils import save_ascii_pcd
    path=tmp_path/'cloud.pcd'; save_ascii_pcd(path,np.array([[0,0,1.],[1,1,1.],[2,2,2.]]))
    result=invoke('cloud','view',path,'--max-points','2',cwd='/tmp')
    assert result.returncode==0, result.stderr
    html=path.with_suffix('.html').read_text()
    assert '<script src=' not in html and 'plotly.js' in html.lower()
    assert 'filtered=3 displayed=2' in result.stdout
    first=html
    assert invoke('cloud','view',path,'--max-points','2').returncode==0
    assert path.with_suffix('.html').read_text()==first


def test_sync_and_monitor_reports(tmp_path):
    assert offsets([2.,1.],[2.01,1.01])==pytest.approx([10,10])
    assert summarize('/main',[],{'/sensor':[]})['topics']['/sensor']['abs_max_ms'] is None
    csv=tmp_path/'monitor.csv';csv.write_text('timestamp,elapsed_s,gpu_pct\nt,0,10\nt,1,\nt,2,30\n')
    out=tmp_path/'summary.json'
    assert invoke('monitor','report',csv,'--json',out).returncode==0
    report=json.loads(out.read_text());assert report['metrics']['gpu_pct']['mean']==20
    assert report['metrics']['gpu_pct']['missing']==1 and report['duration_s']==2


def test_stalled_monitor_producer_is_bounded():
    from tools.lib.monitor import next_ts
    child=subprocess.Popen([sys.executable,'-u','-c','import time;time.sleep(20)'],stdout=subprocess.PIPE,text=True)
    start=time.monotonic()
    try:
        with pytest.raises(TimeoutError): next_ts(child,.05)
        assert time.monotonic()-start<1
    finally:
        child._reader_stop.set(); child.terminate();child.wait(timeout=2)
        child._reader.join(timeout=2);child.stdout.close()


def test_help_and_operational_dry_runs(tmp_path):
    for args in [('cloud','view'),('yolo','filter'),('monitor','jetson'),('ros','sync'),('calibration','convert')]:
        result=invoke(*args,'--help',cwd='/tmp');assert result.returncode==0,result.stderr
    assert invoke('ros','camera-reset','--dry-run').returncode==0
    result=invoke('ros','record','--dry-run')
    assert result.returncode==0 and '/iccp_status' in result.stdout


@pytest.mark.parametrize('shape,boxes,failed,code', [((768,960),True,False,0),((360,640),True,False,0),((10,10),True,False,1),((768,960),False,False,0),((768,960),True,True,1)])
def test_yolo_uses_presets_and_reports_failures(tmp_path, monkeypatch, shape, boxes, failed, code):
    from types import SimpleNamespace
    from tools.lib import yolo
    image=tmp_path/'one.jpg';image.write_bytes(b'image')
    model=tmp_path/'model.pt';model.write_bytes(b'model')
    output=tmp_path/'output.json'
    box=SimpleNamespace(cls=np.array(0),conf=np.array(.9),xyxy=np.array([[0,0,10,40]]))
    class FakeModel:
        names={0:'person'}
        def predict(self,*args,**kwargs):
            if failed: raise ValueError('bad image')
            return [SimpleNamespace(orig_shape=shape,boxes=[box] if boxes else [])]
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=lambda _:FakeModel()))
    monkeypatch.setattr(sys,'argv',['filter','--input',str(tmp_path),'--model',str(model),'--output',str(output)])
    assert yolo.main()==code
    records=json.loads(output.read_text())
    if boxes and not failed and shape in ((768,960),(360,640)):
        assert records[0]['passed'] and records[0]['objects'][0]['class']=='person'
    else: assert records==[]


def test_bag_is_read_once(monkeypatch):
    from types import SimpleNamespace
    from tools.lib.ros import read_bag
    calls=[]
    class Bag:
        def __init__(self,path): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read_messages(self,topics):
            calls.append(topics)
            yield '/a',SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(to_sec=lambda:1.))),None
    monkeypatch.setitem(sys.modules,'rosbag',SimpleNamespace(Bag=Bag))
    assert read_bag('test.bag',['/a','/b'])=={'/a':[1.],'/b':[]}
    assert len(calls)==1


def test_monitor_duration_even_without_first_sample(tmp_path, monkeypatch):
    from tools.lib import monitor
    children=[]
    monkeypatch.setattr(monitor,'find_ts',lambda:'fake')
    def start(*args):
        child=subprocess.Popen([sys.executable,'-u','-c','import time;time.sleep(20)'],stdout=subprocess.PIPE,text=True)
        children.append(child);return child
    monkeypatch.setattr(monitor,'start_ts',start)
    monkeypatch.setattr(sys,'argv',['monitor','--duration','0.05s','-o',str(tmp_path/'monitor.csv')])
    before=time.monotonic()
    assert monitor.main()==1
    assert time.monotonic()-before<2
    assert children[0].poll() is not None


def test_help_blocks_optional_imports():
    code='''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'rospy','rosbag','ultralytics','torch','open3d','plotly'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0,Block())
from tools.lib.cli import main
for args in [[],['cloud','view','--help'],['yolo','filter','--help'],['ros','sync','--help']]:
    try: main(args)
    except SystemExit as exc: assert exc.code==0
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_camera_timeout_does_not_launch(monkeypatch):
    from tools.lib import ros
    calls=[]
    monkeypatch.setattr(sys,'argv',['camera-reset','--nodes','/camera'])
    monkeypatch.setattr(ros.subprocess,'check_output',lambda *a,**k:'/camera\n')
    monkeypatch.setattr(ros.subprocess,'run',lambda *a,**k:calls.append(a[0]))
    def forbidden(*args,**kwargs): raise AssertionError('replacement must not launch')
    monkeypatch.setattr(ros.subprocess,'Popen',forbidden)
    clock=iter([0.,11.])
    monkeypatch.setattr(ros.time,'monotonic',lambda:next(clock))
    with pytest.raises(RuntimeError,match='did not stop'): ros.camera_reset()
    assert calls==[['rosnode','kill','/camera']]


def test_tegrastats_parsing_keeps_missing_fields_empty():
    from tools.lib.monitor import parse_ts
    result=parse_ts('RAM 100/1000MB CPU [10%@1200,off] GR3D_FREQ 40%@600 cpu@45C')
    assert result['ram_used_mb']==100 and result['gpu_pct']==40
    assert result['cpu']==[{'pct':10.,'mhz':1200.},{'pct':None,'mhz':None}]
    assert result['emc_mhz'] is None and result['temp_cpu_c']==45
