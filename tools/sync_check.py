#!/usr/bin/env python3
import argparse,bisect,statistics

IMG=[
"/miivii_gmsl_camera_node/miivii_gmsl/image2",
"/miivii_gmsl_camera_node/miivii_gmsl/image4",
"/miivii_gmsl_camera_node/miivii_gmsl/image5",
"/miivii_gmsl_camera_node/miivii_gmsl/image6",
"/miivii_gmsl_camera_node/miivii_gmsl/image7"]
PCD=["/livox/lidar_lb","/livox/lidar_lf","/livox/lidar_rb",
"/livox/lidar_rf","/rslidar_points"]

def read_bag(path,topics):
    import rosbag
    b=rosbag.Bag(path)
    d={t:[m.header.stamp.to_sec() for _,m,_ in b.read_messages(topics=[t])] for t in topics}
    b.close()
    return d

def read_ros(topics,img):
    import rospy
    from sensor_msgs.msg import Image,PointCloud2
    rospy.init_node("sync_check")
    d={t:[] for t in topics}
    def cb(m,t): d[t].append(m.header.stamp.to_sec())
    for t in topics:
        rospy.Subscriber(t,Image if t in img else PointCloud2,cb,t)
    print("collecting 5s...")
    rospy.sleep(5)
    return d

def main(base,bt,d):
    print(f"\nMain: {base} ({len(bt)} msgs)\nOffset(sensor-main):")
    for k,ts in d.items():
        r=[]
        for t in bt:
            i=bisect.bisect_left(ts,t)
            n=ts[0] if i==0 else ts[-1] if i==len(ts) else min(ts[i-1:i+1],key=lambda x:abs(x-t))
            r.append((n-t)*1000)
        print(f"{k}: median={statistics.median(r):+.3f} ms mean={statistics.mean(r):+.3f} ms")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--img-topics",nargs="+",default=IMG)
    p.add_argument("--pcd-topics",nargs="+",default=PCD)
    p.add_argument("--main-topic",default="/rslidar_points")
    p.add_argument("--bag")
    a=p.parse_args()

    topics=a.img_topics+a.pcd_topics
    if a.main_topic not in topics: topics.append(a.main_topic)

    d=read_bag(a.bag,topics) if a.bag else read_ros(topics,a.img_topics)
    bt=d.pop(a.main_topic)
    main(a.main_topic,bt,d)