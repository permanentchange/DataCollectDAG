rosbag record -O sensor_data.bag --split --duration=60 \
    /miivii_gmsl_camera_node/miivii_gmsl/image2 \
    /miivii_gmsl_camera_node/miivii_gmsl/image4 \
    /miivii_gmsl_camera_node/miivii_gmsl/image5 \
    /miivii_gmsl_camera_node/miivii_gmsl/image6 \
    /miivii_gmsl_camera_node/miivii_gmsl/image7 \
    /rslidar_points \
    /livox/lidar_rf \
    /livox/lidar_rb \
    /livox/lidar_lf \
    /livox/lidar_lb \
    /imu/data \
    /bdstar/nmea_sentence \
    /odom \
    /localization
